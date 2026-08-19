"""SQLite access and a small versioned migration runner.

No ORM. Migrations are plain .sql files named NNN_description.sql, applied in
numeric order inside a transaction and recorded in schema_migrations.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open the database, creating parent directories as needed."""
    path = Path(path)
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def _applied(conn: sqlite3.Connection) -> set[str]:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    return {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}


def migrate(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations. Returns the versions applied by this call."""
    applied = _applied(conn)
    pending = sorted(
        (p for p in migrations_dir.glob("*.sql") if p.stem not in applied),
        key=lambda p: p.name,
    )
    done: list[str] = []
    for migration in pending:
        log.info("applying migration", extra={"migration": migration.stem})
        # executescript commits any open transaction before it runs, so the
        # BEGIN has to live inside the script for the migration to be atomic.
        version = migration.stem.replace("'", "''")
        script = (
            "BEGIN;\n"
            f"{migration.read_text()}\n"
            "INSERT INTO schema_migrations (version, applied_at) "
            f"VALUES ('{version}', '{utc_now()}');\n"
            "COMMIT;"
        )
        try:
            conn.executescript(script)
        except Exception:
            try:
                conn.executescript("ROLLBACK;")
            except sqlite3.OperationalError:
                pass  # nothing was open, the failure happened before BEGIN took
            raise
        done.append(migration.stem)
    return done


def open_database(path: str | Path) -> sqlite3.Connection:
    """Connect and bring the schema up to date. The normal entry point."""
    conn = connect(path)
    migrate(conn)
    return conn


def upsert(
    conn: sqlite3.Connection,
    table: str,
    row: dict[str, Any],
    key_columns: Iterable[str],
) -> None:
    """Insert or update one row, keyed on key_columns.

    This is what makes every sync re-runnable: the same data point always lands
    on the same primary key, so a repeated sync updates in place instead of
    duplicating.
    """
    keys = list(row)
    placeholders = ", ".join("?" for _ in keys)
    key_set = set(key_columns)
    updates = [k for k in keys if k not in key_set]
    conflict = ", ".join(key_columns)
    sql = f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({placeholders})"
    if updates:
        assignments = ", ".join(f"{k} = excluded.{k}" for k in updates)
        sql += f" ON CONFLICT ({conflict}) DO UPDATE SET {assignments}"
    else:
        sql += f" ON CONFLICT ({conflict}) DO NOTHING"
    conn.execute(sql, [row[k] for k in keys])


def query(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return list(conn.execute(sql, tuple(params)))


def query_one(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    cursor = conn.execute(sql, tuple(params))
    return cursor.fetchone()


def iter_rows(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> Iterator[sqlite3.Row]:
    yield from conn.execute(sql, tuple(params))


# --- sync_state helpers -------------------------------------------------


def get_sync_state(conn: sqlite3.Connection, data_type: str) -> sqlite3.Row | None:
    return query_one(conn, "SELECT * FROM sync_state WHERE data_type = ?", (data_type,))


def update_sync_state(conn: sqlite3.Connection, data_type: str, **fields: Any) -> None:
    fields = {k: v for k, v in fields.items() if v is not None}
    fields["updated_at"] = utc_now()
    conn.execute(
        "INSERT INTO sync_state (data_type) VALUES (?) ON CONFLICT (data_type) DO NOTHING",
        (data_type,),
    )
    assignments = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE sync_state SET {assignments} WHERE data_type = ?",
        [*fields.values(), data_type],
    )


def bump_records(conn: sqlite3.Connection, data_type: str, count: int) -> None:
    conn.execute(
        "UPDATE sync_state SET total_records = total_records + ? WHERE data_type = ?",
        (count, data_type),
    )


# --- sync_runs helpers --------------------------------------------------


def start_run(
    conn: sqlite3.Connection,
    mode: str,
    range_start: str | None = None,
    range_end: str | None = None,
) -> int:
    # A run killed without a chance to clean up (SIGKILL, power loss) leaves its
    # row marked running. Reap those here so the run log stays truthful.
    conn.execute(
        "UPDATE sync_runs SET status = 'interrupted', finished_at = ? "
        "WHERE status = 'running'",
        (utc_now(),),
    )
    cursor = conn.execute(
        """
        INSERT INTO sync_runs (mode, started_at, status, range_start, range_end)
        VALUES (?, ?, 'running', ?, ?)
        """,
        (mode, utc_now(), range_start, range_end),
    )
    return int(cursor.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    api_calls: int,
    records: int,
    raw_pages: int,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE sync_runs
           SET finished_at = ?, status = ?, api_calls = ?, records = ?,
               raw_pages = ?, error = ?
         WHERE id = ?
        """,
        (utc_now(), status, api_calls, records, raw_pages, error, run_id),
    )


def record_run_item(
    conn: sqlite3.Connection,
    run_id: int,
    data_type: str,
    status: str,
    api_calls: int,
    records: int,
    method: str | None = None,
    error: str | None = None,
) -> None:
    upsert(
        conn,
        "sync_run_items",
        {
            "run_id": run_id,
            "data_type": data_type,
            "status": status,
            "api_calls": api_calls,
            "records": records,
            "method": method,
            "error": error,
        },
        ("run_id", "data_type"),
    )
