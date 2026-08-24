"""Ingestion orchestration: backfill, incremental sync, and dry run.

Backfill walks backward from today toward the account creation date in per type
chunks, committing and checkpointing after each chunk. Killing a backfill mid
run therefore loses at most the chunk in flight; the next run picks up from the
stored cursor.

Incremental sync re-reads the last few days. That window matters: a sleep
session posts after wake, and a watch that has not synced can backdate data by
a day or two. Re-reading is safe because every write is an idempotent upsert.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from fitbit_pipeline import db
from fitbit_pipeline.api import ApiError, HealthApiClient, data_points, payload_hash
from fitbit_pipeline.auth import AuthError
from fitbit_pipeline.aggregate import rebuild_daily_summary
from fitbit_pipeline.datatypes import DataType, enabled_data_types
from fitbit_pipeline.normalize import normalize_point

log = logging.getLogger(__name__)

# Nothing predates Fitbit itself, so this is a safe floor when the account
# creation date cannot be read.
EARLIEST_POSSIBLE = date(2007, 1, 1)


@dataclass
class TypeResult:
    data_type: str
    records: int = 0
    api_calls: int = 0
    pages: int = 0
    method: str = ""
    status: str = "ok"
    error: str = ""
    dates_touched: set[str] = field(default_factory=set)


@dataclass
class SyncResult:
    mode: str
    run_id: int | None = None
    started_at: str = ""
    finished_at: str = ""
    status: str = "ok"
    api_calls: int = 0
    records: int = 0
    pages: int = 0
    items: list[TypeResult] = field(default_factory=list)

    @property
    def failures(self) -> list[TypeResult]:
        return [item for item in self.items if item.status != "ok"]


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _chunks(start: date, end: date, size_days: int) -> list[tuple[date, date]]:
    """Split [start, end) into chunks, newest first."""
    windows = []
    cursor = end
    while cursor > start:
        chunk_start = max(start, cursor - timedelta(days=size_days))
        windows.append((chunk_start, cursor))
        cursor = chunk_start
    return windows


class Syncer:
    def __init__(
        self,
        conn: sqlite3.Connection,
        client: HealthApiClient,
        *,
        prefer_reconciled: bool = True,
        skip_data_types: Iterable[str] | None = None,
        extra_scopes: Iterable[str] | None = None,
        dry_run: bool = False,
    ) -> None:
        self.conn = conn
        self.client = client
        self.prefer_reconciled = prefer_reconciled
        self.dry_run = dry_run
        self.data_types = enabled_data_types(
            list(extra_scopes or []), list(skip_data_types or [])
        )

    # --- one data type over one range ------------------------------------

    def fetch_range(
        self, data_type: DataType, start: date, end: date, *, persist: bool = True
    ) -> TypeResult:
        """Read [start, end) for one data type, splitting the range on a 5xx.

        The reconcile endpoint returns 500 on some ranges purely because of
        their width: a fourteen day window fails while both seven day halves
        and every individual day in it succeed. Retrying the identical request
        never clears that, and because the backfill walks strictly backward, a
        single unservable chunk would otherwise make every older day for that
        data type permanently unreachable.

        So halve the range and try again. At a single day, where there is
        nothing left to split, fall back to the list method, which serves
        ranges reconcile refuses. Only when both fail on one day is the range
        genuinely unreadable.
        """
        span = (end - start).days
        try:
            return self._fetch_range_once(
                data_type, start, end, persist=persist, prefer_reconciled=self.prefer_reconciled
            )
        except ApiError as exc:
            if exc.status < 500 or span <= 1:
                if exc.status >= 500 and span <= 1 and self.prefer_reconciled:
                    log.warning(
                        "reconcile failed on a single day, falling back to list",
                        extra={"data_type": data_type.api_id, "date": start.isoformat()},
                    )
                    return self._fetch_range_once(
                        data_type, start, end, persist=persist, prefer_reconciled=False
                    )
                raise

        midpoint = start + timedelta(days=span // 2)
        log.warning(
            "splitting a range the API would not serve",
            extra={
                "data_type": data_type.api_id,
                "range": f"{start} to {end}",
                "midpoint": midpoint.isoformat(),
            },
        )
        left = self.fetch_range(data_type, start, midpoint, persist=persist)
        right = self.fetch_range(data_type, midpoint, end, persist=persist)

        merged = TypeResult(data_type=data_type.api_id)
        merged.method = left.method or right.method
        merged.records = left.records + right.records
        merged.api_calls = left.api_calls + right.api_calls
        merged.pages = left.pages + right.pages
        merged.dates_touched = left.dates_touched | right.dates_touched
        return merged

    def _fetch_range_once(
        self,
        data_type: DataType,
        start: date,
        end: date,
        *,
        persist: bool = True,
        prefer_reconciled: bool = True,
    ) -> TypeResult:
        """Read [start, end) for one data type and write what comes back."""
        result = TypeResult(data_type=data_type.api_id)
        calls_before = self.client.api_calls
        page_index = 0

        for method, page in self.client.iter_pages(
            data_type, start, end, prefer_reconciled=prefer_reconciled
        ):
            result.method = method
            points = data_points(page)
            result.pages += 1

            if persist:
                self._store_raw(data_type, method, start, end, page_index, page, len(points))

            for point in points:
                rows = normalize_point(data_type.api_id, data_type.payload_key, point)
                for row in rows:
                    if persist:
                        db.upsert(self.conn, row.table, row.values, row.keys)
                    day = row.values.get("local_date") or row.values.get("date")
                    if day:
                        result.dates_touched.add(str(day))
                result.records += len(rows)
            page_index += 1

        result.api_calls = self.client.api_calls - calls_before
        return result

    def _store_raw(
        self,
        data_type: DataType,
        method: str,
        start: date,
        end: date,
        page_index: int,
        page: dict[str, Any],
        point_count: int,
    ) -> None:
        """Archive the response verbatim.

        The unique index makes this a no-op when a re-run returns an identical
        page, and a new row when the upstream answer actually changed, so late
        arriving data keeps its history.
        """
        self.conn.execute(
            """
            INSERT OR IGNORE INTO raw_payloads
                (data_type, method, range_start, range_end, page_index,
                 fetch_time, point_count, payload_hash, json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data_type.api_id,
                method,
                start.isoformat(),
                end.isoformat(),
                page_index,
                db.utc_now(),
                point_count,
                payload_hash(page),
                json.dumps(page, separators=(",", ":")),
            ),
        )

    # --- incremental ------------------------------------------------------

    def daily(self, window_days: int = 3, end: date | None = None) -> SyncResult:
        """Re-read the last window_days, which is the unattended daily path."""
        end_exclusive = (end or today_utc()) + timedelta(days=1)
        start = end_exclusive - timedelta(days=window_days)
        return self._run("daily", [(dt, [(start, end_exclusive)]) for dt in self.data_types])

    # --- backfill ---------------------------------------------------------

    def resolve_backfill_start(self, configured: str = "") -> date:
        """Lower bound for backfill: config, else the account creation date."""
        if configured:
            return date.fromisoformat(configured)
        try:
            profile = self.client.get_profile()
        except ApiError as exc:
            log.warning(
                "could not read profile, backfilling from the earliest possible date",
                extra={"status": exc.status},
            )
            return EARLIEST_POSSIBLE
        membership = profile.get("membershipStartDate") or {}
        year, month, day = (
            membership.get("year"),
            membership.get("month"),
            membership.get("day"),
        )
        if year and month and day:
            start = date(int(year), int(month), int(day))
            log.info("backfilling to account creation date", extra={"since": start.isoformat()})
            return start
        return EARLIEST_POSSIBLE

    def backfill(
        self,
        start: date,
        end: date | None = None,
        *,
        resume: bool = True,
        max_chunks: int | None = None,
    ) -> SyncResult:
        """Walk backward to `start`, checkpointing after every committed chunk."""
        end_exclusive = (end or today_utc()) + timedelta(days=1)
        plan: list[tuple[DataType, list[tuple[date, date]]]] = []

        for data_type in self.data_types:
            state = db.get_sync_state(self.conn, data_type.api_id)
            cursor = end_exclusive
            if resume and state:
                if state["backfill_complete"] and state["backfill_start"] == start.isoformat():
                    log.info("backfill already complete", extra={"data_type": data_type.api_id})
                    continue
                if state["backfill_cursor"]:
                    stored = date.fromisoformat(state["backfill_cursor"])
                    # Only resume from a cursor that is still inside the range.
                    if start < stored <= end_exclusive:
                        cursor = stored
            windows = _chunks(start, cursor, data_type.chunk_days)
            if max_chunks is not None:
                windows = windows[:max_chunks]
            if windows:
                plan.append((data_type, windows))

        return self._run("backfill", plan, backfill_floor=start)

    # --- shared execution -------------------------------------------------

    def _run(
        self,
        mode: str,
        plan: list[tuple[DataType, list[tuple[date, date]]]],
        backfill_floor: date | None = None,
    ) -> SyncResult:
        result = SyncResult(mode=mode, started_at=db.utc_now())
        if not plan:
            result.finished_at = db.utc_now()
            log.info("nothing to sync")
            return result

        overall_start = min(window[0] for _, windows in plan for window in windows)
        overall_end = max(window[1] for _, windows in plan for window in windows)

        if not self.dry_run:
            result.run_id = db.start_run(
                self.conn, mode, overall_start.isoformat(), overall_end.isoformat()
            )

        touched: set[str] = set()

        for data_type, windows in plan:
            item = TypeResult(data_type=data_type.api_id)
            try:
                for chunk_start, chunk_end in windows:
                    log.info(
                        "fetching",
                        extra={
                            "data_type": data_type.api_id,
                            "from": chunk_start.isoformat(),
                            "to": chunk_end.isoformat(),
                        },
                    )
                    chunk = self.fetch_range(
                        data_type, chunk_start, chunk_end, persist=not self.dry_run
                    )
                    item.records += chunk.records
                    item.api_calls += chunk.api_calls
                    item.pages += chunk.pages
                    item.method = chunk.method or item.method
                    item.dates_touched |= chunk.dates_touched

                    if not self.dry_run:
                        self._checkpoint(
                            data_type,
                            chunk_start,
                            chunk_end,
                            chunk,
                            mode,
                            backfill_floor,
                        )
            except KeyboardInterrupt:
                # Ctrl-C during a long backfill. Checkpoints are already
                # committed, so summarize what was fetched, record what this
                # run achieved, and get out.
                self._abandon_run(
                    result,
                    item,
                    touched,
                    "interrupted",
                    f"interrupted while fetching {data_type.api_id}",
                )
                raise
            except AuthError:
                # Credentials are broken, so every remaining data type would
                # fail the same way. Close the run and let the caller report
                # the remedy instead of logging the same traceback 28 times.
                self._abandon_run(result, item, touched, "failed", "authorization failed")
                raise
            except ApiError as exc:
                item.status = "failed"
                item.error = str(exc)
                log.error(
                    "data type failed",
                    extra={"data_type": data_type.api_id, "status": exc.status},
                )
            except Exception as exc:  # keep going, one bad type is not the run
                item.status = "failed"
                item.error = str(exc)
                log.exception("data type failed", extra={"data_type": data_type.api_id})

            result.items.append(item)
            result.records += item.records
            result.api_calls += item.api_calls
            result.pages += item.pages
            touched |= item.dates_touched

            if not self.dry_run and result.run_id is not None:
                db.record_run_item(
                    self.conn,
                    result.run_id,
                    data_type.api_id,
                    item.status,
                    item.api_calls,
                    item.records,
                    item.method,
                    item.error or None,
                )

        if not self.dry_run and touched:
            rebuild_daily_summary(self.conn, touched)

        failures = result.failures
        result.status = "ok" if not failures else ("partial" if len(failures) < len(result.items) else "failed")
        result.finished_at = db.utc_now()

        if not self.dry_run and result.run_id is not None:
            db.finish_run(
                self.conn,
                result.run_id,
                result.status,
                result.api_calls,
                result.records,
                result.pages,
                "; ".join(f"{f.data_type}: {f.error}" for f in failures) or None,
            )

        log.info(
            "sync finished",
            extra={
                "mode": mode,
                "status": result.status,
                "records": result.records,
                "api_calls": result.api_calls,
                "pages": result.pages,
                "days_touched": len(touched),
            },
        )
        return result

    def _abandon_run(
        self,
        result: SyncResult,
        item: TypeResult,
        touched: set[str],
        status: str,
        error: str,
    ) -> None:
        """Close out a run that will not finish, without losing its work.

        The derived daily_summary is normally rebuilt at the end of a run. A run
        that dies partway still wrote rows, so rebuild for what it touched
        before giving up, or the dashboard would show stale days until the next
        run that happens to complete.
        """
        if self.dry_run or result.run_id is None:
            return
        all_touched = touched | item.dates_touched
        if all_touched:
            rebuild_daily_summary(self.conn, all_touched)
        db.finish_run(
            self.conn,
            result.run_id,
            status,
            result.api_calls + item.api_calls,
            result.records + item.records,
            result.pages + item.pages,
            error,
        )

    def _checkpoint(
        self,
        data_type: DataType,
        chunk_start: date,
        chunk_end: date,
        chunk: TypeResult,
        mode: str,
        backfill_floor: date | None,
    ) -> None:
        """Persist progress so an interrupted run resumes from here."""
        fields: dict[str, Any] = {"last_success_at": db.utc_now()}

        state = db.get_sync_state(self.conn, data_type.api_id)
        synced_through = state["synced_through"] if state else None
        newest = (chunk_end - timedelta(days=1)).isoformat()
        if synced_through is None or newest > synced_through:
            fields["synced_through"] = newest

        if chunk.dates_touched:
            earliest = min(chunk.dates_touched)
            known = state["earliest_data_date"] if state else None
            if known is None or earliest < known:
                fields["earliest_data_date"] = earliest

        if mode == "backfill" and backfill_floor is not None:
            fields["backfill_start"] = backfill_floor.isoformat()
            fields["backfill_cursor"] = chunk_start.isoformat()
            if chunk_start <= backfill_floor:
                fields["backfill_complete"] = 1

        db.update_sync_state(self.conn, data_type.api_id, **fields)
        db.bump_records(self.conn, data_type.api_id, chunk.records)


def dry_run_report(result: SyncResult) -> str:
    lines = [f"dry run: {result.records} records would be written, {result.api_calls} API calls"]
    for item in sorted(result.items, key=lambda i: i.data_type):
        status = "" if item.status == "ok" else f"  [{item.status}: {item.error[:120]}]"
        lines.append(
            f"  {item.data_type:38s} {item.records:6d} records  "
            f"{item.pages} page(s) via {item.method or 'n/a'}{status}"
        )
    return "\n".join(lines)
