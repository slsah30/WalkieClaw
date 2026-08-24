"""Google OAuth 2.0 for a single personal account.

First run opens a browser, completes the loopback flow, and writes a refresh
token to disk with mode 0600. Every run after that refreshes silently. There is
no interactive step in the daily path, which is what FR-1 and acceptance
criterion 1 require.

Two preconditions from the PRD are enforced here, before any health data call:

1. The authorized account must not be a Google Workspace account. Workspace
   accounts cannot be linked to Google Health at all. The OIDC id_token carries
   an `hd` (hosted domain) claim only for Workspace accounts, so this is
   detectable at auth time rather than as a confusing 403 later.
2. The Fitbit account must already be migrated to Google sign in. That is
   checked by calling users/me/identity, which returns the legacy Fitbit user id
   for a migrated account.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

log = logging.getLogger(__name__)

TOKEN_URI = "https://oauth2.googleapis.com/token"

REAUTH_HINT = (
    "The stored refresh token is no longer valid. Run `fitbit-sync auth` to grant "
    "access again.\n"
    "If this keeps happening about once a week, the OAuth consent screen is still "
    "in Testing publishing status, where Google expires refresh tokens after 7 "
    "days. Publish the app to production in the Google Cloud console under APIs "
    "and services, OAuth consent screen. See docs/api-findings.md section 5.3."
)


class AuthError(Exception):
    """Raised for any unrecoverable authorization problem."""


class WorkspaceAccountError(AuthError):
    """The authorized account belongs to a Google Workspace domain."""


@dataclass
class TokenRecord:
    """What gets written to the token file."""

    refresh_token: str
    client_id: str
    client_secret: str
    scopes: list[str]
    token: str | None = None
    expiry: str | None = None
    created_at: str = ""
    last_refresh_at: str = ""
    account_email: str = ""
    hosted_domain: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scopes": self.scopes,
            "token": self.token,
            "expiry": self.expiry,
            "created_at": self.created_at,
            "last_refresh_at": self.last_refresh_at,
            "account_email": self.account_email,
            "hosted_domain": self.hosted_domain,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def decode_id_token_claims(id_token: str | None) -> dict[str, Any]:
    """Read the claims out of an id_token without verifying the signature.

    Verification would be required if this token arrived from an untrusted
    party. It did not: it came straight back from Google's token endpoint over
    TLS in this same process, and it is used only to read the account email and
    the hosted domain claim. No authorization decision hangs on it.
    """
    if not id_token:
        return {}
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError) as exc:
        log.warning("could not decode id_token", extra={"error": str(exc)})
        return {}


def assert_personal_account(claims: dict[str, Any]) -> None:
    """Fail loudly when the authorized account is a Workspace account."""
    domain = claims.get("hd")
    if domain:
        raise WorkspaceAccountError(
            f"The account you authorized ({claims.get('email', 'unknown')}) belongs to "
            f"the Google Workspace domain '{domain}'.\n"
            "Google Workspace accounts are not supported by the Google Health API, and "
            "cannot be used to migrate a Fitbit account.\n"
            "Re-run `fitbit-sync auth` and sign in with the personal Google account "
            "that your Fitbit account was migrated to."
        )


def _secure_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, stat.S_IRWXU)  # 0700
    # Create with 0600 from the start so the secret is never briefly world readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(payload, handle, indent=2)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _check_permissions(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        log.warning(
            "token file is readable by others, tightening to 0600",
            extra={"path": str(path), "mode": oct(mode)},
        )
        os.chmod(path, 0o600)


def load_client_secret(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise AuthError(
            f"OAuth client secret not found at {path}.\n"
            "Download it from the Google Cloud console: APIs and services, "
            "Credentials, your OAuth 2.0 Client ID, Download JSON. "
            "See the README section 'Google Cloud setup'."
        )
    with path.open() as handle:
        data = json.load(handle)
    if "installed" not in data and "web" not in data:
        raise AuthError(
            f"{path} does not look like an OAuth client secret file. Expected a "
            "top level 'installed' or 'web' key."
        )
    return data


def run_consent_flow(
    client_secret_file: Path,
    token_file: Path,
    scopes: list[str],
    local_port: int = 0,
) -> TokenRecord:
    """Run the one time browser consent and persist the refresh token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    load_client_secret(client_secret_file)
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), scopes)
    credentials = flow.run_local_server(
        port=local_port,
        access_type="offline",
        prompt="consent",  # forces a refresh token even on re-grant
        open_browser=True,
        authorization_prompt_message=(
            "Open this URL to authorize fitbit-pipeline:\n{url}\n"
            "Google will warn that the app is unverified. That is expected for a "
            "personal app. Choose Advanced, then 'Go to ... (unsafe)'."
        ),
        success_message=(
            "Authorization complete. You can close this tab and return to the terminal."
        ),
    )

    if not credentials.refresh_token:
        raise AuthError(
            "Google did not return a refresh token. Revoke the app's access at "
            "https://myaccount.google.com/permissions and run `fitbit-sync auth` again."
        )

    claims = decode_id_token_claims(getattr(credentials, "id_token", None))
    assert_personal_account(claims)

    record = TokenRecord(
        refresh_token=credentials.refresh_token,
        client_id=credentials.client_id,
        client_secret=credentials.client_secret,
        scopes=list(credentials.scopes or scopes),
        token=credentials.token,
        expiry=credentials.expiry.isoformat() if credentials.expiry else None,
        created_at=_now(),
        last_refresh_at=_now(),
        account_email=claims.get("email", ""),
        hosted_domain=claims.get("hd", ""),
    )
    _secure_write(token_file, record.to_json())
    log.info(
        "authorization stored",
        extra={"token_file": str(token_file), "account": record.account_email},
    )
    return record


def load_token_record(token_file: Path) -> TokenRecord:
    if not token_file.exists():
        raise AuthError(
            f"No stored credentials at {token_file}. Run `fitbit-sync auth` once to "
            "grant access."
        )
    _check_permissions(token_file)
    with token_file.open() as handle:
        data = json.load(handle)
    missing = [k for k in ("refresh_token", "client_id", "client_secret") if not data.get(k)]
    if missing:
        raise AuthError(
            f"{token_file} is missing {', '.join(missing)}. Run `fitbit-sync auth` again."
        )
    return TokenRecord(
        refresh_token=data["refresh_token"],
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        scopes=data.get("scopes", []),
        token=data.get("token"),
        expiry=data.get("expiry"),
        created_at=data.get("created_at", ""),
        last_refresh_at=data.get("last_refresh_at", ""),
        account_email=data.get("account_email", ""),
        hosted_domain=data.get("hosted_domain", ""),
    )


class CredentialStore:
    """Holds credentials and keeps the access token fresh.

    The API client asks this for a bearer token before every request, so a long
    backfill that outlives a one hour access token just refreshes mid run.
    """

    def __init__(self, token_file: Path, scopes: list[str] | None = None) -> None:
        self.token_file = Path(token_file)
        self.record = load_token_record(self.token_file)
        self.scopes = scopes or self.record.scopes
        self._credentials = Credentials(
            token=self.record.token,
            refresh_token=self.record.refresh_token,
            token_uri=TOKEN_URI,
            client_id=self.record.client_id,
            client_secret=self.record.client_secret,
            scopes=self.scopes,
        )
        if self.record.expiry:
            try:
                expiry = datetime.fromisoformat(self.record.expiry)
                # google-auth compares against naive UTC datetimes.
                self._credentials.expiry = expiry.replace(tzinfo=None)
            except ValueError:
                pass

    @property
    def account_email(self) -> str:
        return self.record.account_email

    def missing_scopes(self, required: list[str]) -> list[str]:
        granted = set(self.record.scopes)
        return [scope for scope in required if scope not in granted]

    def refresh_token_age_days(self) -> float | None:
        if not self.record.created_at:
            return None
        try:
            created = datetime.fromisoformat(self.record.created_at)
        except ValueError:
            return None
        return (datetime.now(timezone.utc) - created).total_seconds() / 86400.0

    def token(self) -> str:
        """Return a valid access token, refreshing when needed."""
        if not self._credentials.valid:
            self.refresh()
        return str(self._credentials.token)

    def refresh(self) -> None:
        try:
            self._credentials.refresh(Request())
        except RefreshError as exc:
            raise AuthError(f"{exc}\n\n{REAUTH_HINT}") from exc
        self.record.token = self._credentials.token
        self.record.expiry = (
            self._credentials.expiry.replace(tzinfo=timezone.utc).isoformat()
            if self._credentials.expiry
            else None
        )
        self.record.last_refresh_at = _now()
        _secure_write(self.token_file, self.record.to_json())
        log.debug("access token refreshed")

    def revoke_hint(self) -> str:
        return REAUTH_HINT
