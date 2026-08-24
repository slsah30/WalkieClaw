"""Command line entry point: fitbit-sync.

    fitbit-sync auth        one time browser consent, then never again
    fitbit-sync doctor      check preconditions and report what is wrong
    fitbit-sync backfill    full history, resumable
    fitbit-sync daily       incremental sync, the unattended path
    fitbit-sync serve       local dashboard
    fitbit-sync status      what has been fetched so far
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from fitbit_pipeline import __version__, db
from fitbit_pipeline.api import BASE_URL, ApiError, HealthApiClient
from fitbit_pipeline.auth import (
    AuthError,
    CredentialStore,
    assert_personal_account,
    run_consent_flow,
)
from fitbit_pipeline.config import Config, ConfigError, load_config
from fitbit_pipeline.datatypes import enabled_data_types
from fitbit_pipeline.logging_setup import setup_logging
from fitbit_pipeline.sync import Syncer, dry_run_report

log = logging.getLogger("fitbit_pipeline")

# Testing mode refresh tokens die after 7 days. Warn before that bites.
REFRESH_TOKEN_WARN_DAYS = 6.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fitbit-sync",
        description="Personal Fitbit pipeline on the Google Health API v4.",
    )
    parser.add_argument("--config", help="path to config.toml (default: ./config.toml)")
    parser.add_argument("--log-format", choices=("text", "json"), help="override log format")
    parser.add_argument("--log-level", help="override log level, for example DEBUG")
    parser.add_argument("--version", action="version", version=f"fitbit-pipeline {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth", help="grant access once through the browser")
    auth.add_argument(
        "--force",
        action="store_true",
        help="re-run consent even when a valid token is already stored",
    )

    sub.add_parser("doctor", help="check configuration, credentials, and preconditions")

    backfill = sub.add_parser("backfill", help="fetch full history, resumable")
    backfill.add_argument("--since", help="lower bound date, defaults to account creation")
    backfill.add_argument(
        "--restart", action="store_true", help="ignore stored checkpoints and start over"
    )
    backfill.add_argument(
        "--max-chunks", type=int, help="stop after this many chunks per data type"
    )
    backfill.add_argument("--data-type", action="append", help="limit to one data type, repeatable")

    daily = sub.add_parser("daily", help="incremental sync of the last few days")
    daily.add_argument("--days", type=int, help="override sync.window_days")
    daily.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch one day and print what would be written, without writing",
    )
    daily.add_argument("--data-type", action="append", help="limit to one data type, repeatable")

    serve = sub.add_parser("serve", help="run the local dashboard")
    serve.add_argument("--host", help="override the bind address")
    serve.add_argument("--port", type=int, help="override the port")

    sub.add_parser("status", help="show sync state and recent runs")
    sub.add_parser(
        "rebuild",
        help="recompute daily_summary from the normalized tables, for every day",
    )
    return parser


def _skip_list(config: Config, only: list[str] | None) -> list[str]:
    """Turn a --data-type allowlist into the skip list the Syncer expects."""
    skip = list(config.sync.skip_data_types)
    if only:
        wanted = set(only)
        available = {dt.api_id for dt in enabled_data_types(config.auth.extra_scopes)}
        unknown = wanted - available
        if unknown:
            raise SystemExit(
                f"Unknown or not enabled data type(s): {', '.join(sorted(unknown))}\n"
                f"Enabled: {', '.join(sorted(available))}"
            )
        skip.extend(sorted(available - wanted))
    return skip


def _client(config: Config) -> tuple[HealthApiClient, CredentialStore]:
    credentials = CredentialStore(config.auth.token_file, config.auth.scopes())
    client = HealthApiClient(
        credentials,
        base_url=config.sync.base_url or BASE_URL,
        data_source_family=config.sync.data_source_family,
        max_requests_per_minute=config.sync.max_requests_per_minute,
        max_retries=config.sync.max_retries,
    )
    return client, credentials


def _syncer(config: Config, conn, only: list[str] | None, dry_run: bool = False):
    client, _credentials = _client(config)
    return Syncer(
        conn,
        client,
        prefer_reconciled=config.sync.prefer_reconciled,
        skip_data_types=_skip_list(config, only),
        extra_scopes=config.auth.extra_scopes,
        dry_run=dry_run,
    )


# --- commands -------------------------------------------------------------


def command_auth(args: argparse.Namespace, config: Config) -> int:
    if config.auth.token_file.exists() and not args.force:
        try:
            store = CredentialStore(config.auth.token_file, config.auth.scopes())
            missing = store.missing_scopes(config.auth.scopes())
            if not missing:
                store.token()
                print(f"Already authorized as {store.account_email or 'the stored account'}.")
                print("Nothing to do. Use --force to grant again.")
                return 0
            print(f"Stored token is missing {len(missing)} scope(s), re-running consent.")
        except AuthError as exc:
            print(f"Stored credentials unusable: {exc}\nRunning consent again.\n")

    record = run_consent_flow(
        config.auth.client_secret_file,
        config.auth.token_file,
        config.auth.scopes(),
        config.auth.oauth_local_port,
    )
    print(f"Authorized as {record.account_email or 'unknown account'}.")
    print(f"Refresh token written to {config.auth.token_file} with mode 0600.")

    # Confirm the Fitbit account really is migrated to Google sign in.
    client, _ = _client(config)
    try:
        identity = client.get_identity()
        legacy = identity.get("legacyUserId")
        if legacy:
            print(f"Fitbit account is migrated. Legacy Fitbit user id: {legacy}")
        else:
            print(
                "Warning: the Health API returned no legacy Fitbit user id. If this account has "
                "Fitbit history, confirm the Fitbit account was migrated to Google sign in."
            )
    except ApiError as exc:
        print(f"Warning: could not read users/me/identity ({exc.status}). Run `fitbit-sync doctor`.")
    finally:
        client.close()
    return 0


def command_doctor(args: argparse.Namespace, config: Config) -> int:
    problems = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal problems
        print(f"  [{'ok' if ok else 'FAIL'}] {label}{': ' + detail if detail else ''}")
        if not ok:
            problems += 1

    print(f"Config: {config.source_path or 'defaults, no config.toml found'}")
    print("\nFiles")
    check("client secret present", config.auth.client_secret_file.exists(), str(config.auth.client_secret_file))
    check("token file present", config.auth.token_file.exists(), str(config.auth.token_file))
    if config.auth.token_file.exists():
        mode = oct(config.auth.token_file.stat().st_mode & 0o777)
        check("token file permissions are 0600", mode == "0o600", mode)

    print("\nDatabase")
    conn = db.open_database(config.database.path)
    tables = db.query_one(conn, "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table'")
    check("schema present", tables["n"] > 5, f"{tables['n']} tables at {config.database.path}")
    days = db.query_one(conn, "SELECT COUNT(*) AS n FROM daily_summary")["n"]
    print(f"  daily_summary rows: {days}")

    print("\nCredentials")
    try:
        store = CredentialStore(config.auth.token_file, config.auth.scopes())
    except AuthError as exc:
        check("stored credentials usable", False, str(exc).splitlines()[0])
        print("\nRun `fitbit-sync auth` to grant access.")
        return 1

    missing = store.missing_scopes(config.auth.scopes())
    check("all configured scopes granted", not missing, ", ".join(missing) if missing else "")

    age = store.refresh_token_age_days()
    if age is not None:
        stale = age > REFRESH_TOKEN_WARN_DAYS
        print(f"  refresh token age: {age:.1f} days")
        if stale:
            print(
                "  note: if refreshes start failing around day 7, the OAuth consent screen is "
                "still in Testing publishing status. Publish it to production. "
                "See docs/api-findings.md section 5.3."
            )

    try:
        store.token()
        check("access token refresh", True)
    except AuthError as exc:
        check("access token refresh", False, str(exc).splitlines()[0])
        return 1

    print("\nAccount preconditions")
    try:
        assert_personal_account({"hd": store.record.hosted_domain, "email": store.account_email})
        check("personal Google account, not Workspace", True, store.account_email or "unknown")
    except AuthError as exc:
        check("personal Google account, not Workspace", False, str(exc).splitlines()[0])

    client, _ = _client(config)
    try:
        identity = client.get_identity()
        check(
            "Fitbit account migrated to Google sign in",
            bool(identity.get("legacyUserId")),
            f"legacy id {identity.get('legacyUserId')}" if identity.get("legacyUserId") else
            "no legacyUserId returned",
        )
        settings = client.get_settings()
        print(f"  account timezone: {settings.get('timeZone', 'unknown')}")
        profile = client.get_profile()
        membership = profile.get("membershipStartDate") or {}
        if membership:
            print(
                "  account created: "
                f"{membership.get('year')}-{membership.get('month'):02d}-{membership.get('day'):02d}"
            )
        devices = client.list_paired_devices().get("pairedDevices") or []
        for device in devices:
            print(
                f"  paired device: {device.get('deviceVersion', 'unknown')} "
                f"battery {device.get('batteryLevel', '?')}% last sync {device.get('lastSyncTime', '?')}"
            )
    except ApiError as exc:
        check("Health API reachable", False, f"HTTP {exc.status}")
    finally:
        client.close()
        conn.close()

    print(f"\n{problems} problem(s) found.")
    return 1 if problems else 0


def command_backfill(args: argparse.Namespace, config: Config) -> int:
    conn = db.open_database(config.database.path)
    syncer = _syncer(config, conn, args.data_type)
    try:
        start = (
            date.fromisoformat(args.since)
            if args.since
            else syncer.resolve_backfill_start(config.sync.backfill_start)
        )
        print(f"Backfilling from {start.isoformat()} to today. Safe to interrupt and re-run.")
        result = syncer.backfill(
            start, resume=not args.restart, max_chunks=args.max_chunks
        )
        _print_result(result)
        return 0 if result.status == "ok" else 1
    finally:
        syncer.client.close()
        conn.close()


def command_daily(args: argparse.Namespace, config: Config) -> int:
    conn = db.open_database(config.database.path)
    dry_run = bool(args.dry_run)
    window = 1 if dry_run else (args.days or config.sync.window_days)
    syncer = _syncer(config, conn, args.data_type, dry_run=dry_run)
    try:
        result = syncer.daily(window_days=window)
        if dry_run:
            print(dry_run_report(result))
        else:
            _print_result(result)
        return 0 if result.status == "ok" else 1
    finally:
        syncer.client.close()
        conn.close()


def command_serve(args: argparse.Namespace, config: Config) -> int:
    import uvicorn

    from fitbit_pipeline.reports.app import create_app

    if not Path(config.database.path).exists():
        print(
            f"No database at {config.database.path}. Run `fitbit-sync daily` or "
            "`fitbit-sync backfill` first."
        )
        return 1

    port = args.port or config.server.port
    if args.host:
        host, reach = args.host, "explicit --host"
    elif config.server.tailscale:
        from fitbit_pipeline.net import TailscaleError, tailscale_ipv4

        try:
            host = tailscale_ipv4()
        except TailscaleError as exc:
            print(f"server.tailscale is set but no tailnet address was found.\n{exc}")
            return 1
        reach = "tailnet only"
    elif config.server.expose_lan:
        host, reach = config.server.bind_host(), "every interface, LAN included"
    else:
        host, reach = config.server.bind_host(), "this machine only"

    if host not in {"127.0.0.1", "localhost"}:
        print(
            f"The dashboard has no authentication. Binding {host} makes your health "
            f"data readable by anything that can reach it ({reach})."
        )
    print(f"Dashboard on http://{host}:{port}  ({reach})")
    uvicorn.run(create_app(config), host=host, port=port, log_level=config.logging.level.lower())
    return 0


def command_status(args: argparse.Namespace, config: Config) -> int:
    conn = db.open_database(config.database.path)
    try:
        states = db.query(conn, "SELECT * FROM sync_state ORDER BY data_type")
        if not states:
            print("Nothing synced yet. Run `fitbit-sync backfill` or `fitbit-sync daily`.")
            return 0
        print(f"{'data type':38s} {'through':11s} {'earliest':11s} {'backfill':12s} {'records':>9s}")
        for state in states:
            backfill = (
                "complete"
                if state["backfill_complete"]
                else (f"at {state['backfill_cursor']}" if state["backfill_cursor"] else "not started")
            )
            print(
                f"{state['data_type']:38s} {state['synced_through'] or '-':11s} "
                f"{state['earliest_data_date'] or '-':11s} {backfill:12s} "
                f"{state['total_records']:9d}"
            )
        print()
        for run in db.query(conn, "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 5"):
            print(
                f"run {run['id']:4d} {run['mode']:9s} {run['status']:8s} "
                f"{run['started_at']} records={run['records']} calls={run['api_calls']}"
            )
        return 0
    finally:
        conn.close()


def command_rebuild(args: argparse.Namespace, config: Config) -> int:
    """Re-derive daily_summary from scratch.

    Useful after restoring a database, after a change to the aggregation logic,
    or if a series of interrupted runs left gaps in the derived table.
    """
    from fitbit_pipeline.aggregate import rebuild_daily_summary

    conn = db.open_database(config.database.path)
    try:
        days = rebuild_daily_summary(conn)
        print(f"Rebuilt daily_summary for {days} day(s).")
        return 0
    finally:
        conn.close()


def _print_result(result) -> None:
    print(
        f"{result.mode}: {result.status}, {result.records} records, "
        f"{result.api_calls} API calls, {result.pages} pages"
    )
    for item in sorted(result.items, key=lambda i: i.data_type):
        if item.records or item.status != "ok":
            marker = " " if item.status == "ok" else "!"
            print(f" {marker} {item.data_type:38s} {item.records:7d}  {item.error[:90]}")


COMMANDS = {
    "auth": command_auth,
    "doctor": command_doctor,
    "backfill": command_backfill,
    "daily": command_daily,
    "serve": command_serve,
    "status": command_status,
    "rebuild": command_rebuild,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    setup_logging(
        args.log_format or config.logging.format,
        args.log_level or config.logging.level,
        config.logging.file,
    )

    try:
        return COMMANDS[args.command](args, config)
    except AuthError as exc:
        print(f"\nAuthorization problem:\n{exc}", file=sys.stderr)
        return 3
    except ApiError as exc:
        print(f"\nAPI error {exc.status}: {exc}", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        print("\nInterrupted. Progress is checkpointed, re-run to resume.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
