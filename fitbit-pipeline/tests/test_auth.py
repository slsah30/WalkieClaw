import base64
import json
import os
import stat
from pathlib import Path

import pytest

from fitbit_pipeline import auth


def id_token(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def test_claims_are_read_out_of_the_id_token():
    assert auth.decode_id_token_claims(id_token({"email": "a@b.com"}))["email"] == "a@b.com"
    assert auth.decode_id_token_claims(None) == {}
    assert auth.decode_id_token_claims("garbage") == {}


def test_a_workspace_account_is_rejected_with_a_usable_message():
    with pytest.raises(auth.WorkspaceAccountError) as excinfo:
        auth.assert_personal_account({"email": "t@corp.com", "hd": "corp.com"})
    message = str(excinfo.value)
    assert "corp.com" in message
    assert "personal Google account" in message


def test_a_personal_account_passes():
    auth.assert_personal_account({"email": "t@gmail.com"})
    auth.assert_personal_account({})


def test_tokens_are_written_0600_inside_a_0700_directory(tmp_path):
    path = tmp_path / "nested" / "token.json"
    auth._secure_write(path, {"refresh_token": "x"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_a_loose_token_file_is_tightened_on_read(tmp_path):
    path = tmp_path / "token.json"
    path.write_text(json.dumps({"refresh_token": "r", "client_id": "c", "client_secret": "s"}))
    os.chmod(path, 0o644)
    auth.load_token_record(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_missing_token_file_tells_you_what_to_run(tmp_path):
    with pytest.raises(auth.AuthError, match="fitbit-sync auth"):
        auth.load_token_record(tmp_path / "absent.json")


def test_an_incomplete_token_file_names_what_is_missing(tmp_path):
    path = tmp_path / "token.json"
    path.write_text(json.dumps({"client_id": "c"}))
    with pytest.raises(auth.AuthError, match="refresh_token"):
        auth.load_token_record(path)


def test_a_missing_client_secret_points_at_the_console(tmp_path):
    with pytest.raises(auth.AuthError, match="Google Cloud console"):
        auth.load_client_secret(tmp_path / "client_secret.json")


def test_a_client_secret_of_the_wrong_shape_is_rejected(tmp_path):
    path = tmp_path / "client_secret.json"
    path.write_text(json.dumps({"something": "else"}))
    with pytest.raises(auth.AuthError, match="installed"):
        auth.load_client_secret(path)


def test_the_reauth_hint_explains_the_seven_day_testing_mode_trap():
    assert "7" in auth.REAUTH_HINT
    assert "Testing" in auth.REAUTH_HINT


def test_scope_gaps_are_detectable(tmp_path):
    path = tmp_path / "token.json"
    auth._secure_write(
        path,
        {
            "refresh_token": "r",
            "client_id": "c",
            "client_secret": "s",
            "scopes": ["a", "b"],
            "created_at": "2026-08-01T00:00:00+00:00",
        },
    )
    store = auth.CredentialStore(path)
    assert store.missing_scopes(["a", "c"]) == ["c"]
    assert store.refresh_token_age_days() is not None
