from datetime import date

import httpx
import pytest

from tests.conftest import load_fixture

from fitbit_pipeline import db
from fitbit_pipeline.api import ApiError
from fitbit_pipeline.datatypes import BY_ID
from fitbit_pipeline.sync import Syncer, _chunks

DAY = date(2026, 8, 17)

FIXTURE_PAGES = {
    "steps": [load_fixture("steps.json")],
    "heart-rate": [load_fixture("heart_rate.json")],
    "sleep": [load_fixture("sleep.json")],
    "daily-resting-heart-rate": [load_fixture("daily_resting_heart_rate.json")],
    "daily-heart-rate-variability": [load_fixture("daily_heart_rate_variability.json")],
    "active-zone-minutes": [load_fixture("active_zone_minutes.json")],
    "exercise": [load_fixture("exercise.json")],
    "weight": [load_fixture("weight.json")],
    "daily-oxygen-saturation": [load_fixture("daily_oxygen_saturation.json")],
    "daily-respiratory-rate": [load_fixture("daily_respiratory_rate.json")],
    "daily-sleep-temperature-derivations": [
        load_fixture("daily_sleep_temperature_derivations.json")
    ],
}


def make_syncer(conn, fixture_client, **kwargs):
    client = fixture_client(FIXTURE_PAGES)
    return Syncer(conn, client, **kwargs)


def test_chunks_walk_backward_and_cover_the_range():
    windows = _chunks(date(2026, 8, 1), date(2026, 8, 20), 7)
    assert windows[0] == (date(2026, 8, 13), date(2026, 8, 20))
    assert windows[-1][0] == date(2026, 8, 1)
    # Contiguous, no gaps.
    for earlier, later in zip(windows[1:], windows[:-1]):
        assert earlier[1] == later[0]


def test_daily_sync_writes_every_domain(conn, fixture_client):
    syncer = make_syncer(conn, fixture_client)
    result = syncer.daily(window_days=1, end=DAY)

    assert result.status == "ok"
    assert result.records > 0
    assert db.query(conn, "SELECT * FROM activity_intervals WHERE data_type = 'steps'")
    assert len(db.query(conn, "SELECT * FROM heartrate_intraday")) == 2
    assert len(db.query(conn, "SELECT * FROM sleep_sessions")) == 1
    assert len(db.query(conn, "SELECT * FROM sleep_stages")) == 5
    assert db.query_one(conn, "SELECT bpm FROM resting_hr WHERE date = ?", (DAY.isoformat(),))["bpm"] == 54
    assert db.query_one(conn, "SELECT * FROM workouts")["exercise_type"] == "RUNNING"


def test_reruns_produce_zero_duplicate_rows(conn, fixture_client):
    def counts():
        return {
            table: db.query_one(conn, f"SELECT COUNT(*) AS n FROM {table}")["n"]
            for table in (
                "activity_intervals",
                "heartrate_intraday",
                "sleep_sessions",
                "sleep_stages",
                "workouts",
                "resting_hr",
                "weight",
                "daily_summary",
            )
        }

    make_syncer(conn, fixture_client).daily(window_days=1, end=DAY)
    first = counts()
    for _ in range(3):
        make_syncer(conn, fixture_client).daily(window_days=1, end=DAY)
    assert counts() == first


def test_identical_raw_pages_are_archived_once(conn, fixture_client):
    make_syncer(conn, fixture_client).daily(window_days=1, end=DAY)
    first = db.query_one(conn, "SELECT COUNT(*) AS n FROM raw_payloads")["n"]
    assert first > 0
    make_syncer(conn, fixture_client).daily(window_days=1, end=DAY)
    assert db.query_one(conn, "SELECT COUNT(*) AS n FROM raw_payloads")["n"] == first


def test_a_changed_upstream_page_is_archived_as_a_new_version(conn, fixture_client):
    syncer = Syncer(conn, fixture_client({"steps": [load_fixture("steps.json")]}), skip_data_types=[
        dt for dt in ("heart-rate", "sleep", "exercise") if dt
    ])
    syncer.fetch_range(BY_ID["steps"], DAY, date(2026, 8, 18))
    before = db.query_one(conn, "SELECT COUNT(*) AS n FROM raw_payloads")["n"]

    changed = load_fixture("steps.json")
    changed["dataPoints"][0]["steps"]["count"] = "999"
    syncer2 = Syncer(conn, fixture_client({"steps": [changed]}))
    syncer2.fetch_range(BY_ID["steps"], DAY, date(2026, 8, 18))

    assert db.query_one(conn, "SELECT COUNT(*) AS n FROM raw_payloads")["n"] == before + 1
    # The normalized row was updated in place rather than duplicated.
    rows = db.query(conn, "SELECT value FROM activity_intervals WHERE data_type='steps' ORDER BY start_time")
    assert rows[0]["value"] == 999.0


def test_daily_summary_is_rebuilt_from_the_normalized_tables(conn, fixture_client):
    make_syncer(conn, fixture_client).daily(window_days=1, end=DAY)
    summary = db.query_one(conn, "SELECT * FROM daily_summary WHERE date = ?", (DAY.isoformat(),))

    assert summary["steps"] == 1592  # 412 + 1180
    assert summary["resting_hr"] == 54
    assert summary["hr_min"] == 62 and summary["hr_max"] == 118
    assert summary["hr_sample_count"] == 2
    assert summary["azm_total"] == 3  # 1 fat burn + 2 cardio
    assert summary["azm_cardio"] == 2
    assert summary["sleep_minutes_asleep"] == 442.0
    assert summary["sleep_efficiency"] == pytest.approx(100 * 442 / 456)
    assert summary["hrv_rmssd_ms"] == 41.7
    assert summary["spo2_avg_percent"] == 95.8
    assert summary["breathing_rate"] == 14.6
    assert summary["skin_temp_delta_c"] == pytest.approx(-0.3)
    assert summary["workout_count"] == 1
    assert summary["weight_grams"] == pytest.approx(81646.6)


def test_backfill_checkpoints_after_every_chunk(conn, fixture_client):
    syncer = Syncer(conn, fixture_client(FIXTURE_PAGES), skip_data_types=["heart-rate"])
    syncer.backfill(start=date(2026, 8, 1), end=DAY)

    state = db.get_sync_state(conn, "steps")
    assert state["backfill_complete"] == 1
    assert state["backfill_start"] == "2026-08-01"
    assert state["backfill_cursor"] == "2026-08-01"
    assert state["synced_through"] == DAY.isoformat()
    assert state["earliest_data_date"] == "2026-08-17"


def test_backfill_resumes_where_an_interrupted_run_stopped(conn, fixture_client):
    """Kill a backfill mid run, then prove the next one continues, not restarts."""
    calls: list[httpx.Request] = []
    failures = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        # Drop the connection on the third steps request, simulating a kill.
        # A 500 would not do: fetch_range now splits an unservable range and
        # recovers from it, which is the opposite of an interruption.
        steps_calls = [c for c in calls if "/steps/" in c.url.path]
        if len(steps_calls) == 3 and failures["n"] == 0:
            failures["n"] += 1
            raise httpx.ConnectError("connection lost", request=request)
        return httpx.Response(200, json={"dataPoints": []})

    from fitbit_pipeline.api import HealthApiClient
    from tests.conftest import FakeCredentials

    def build_client():
        client = HealthApiClient(
            FakeCredentials(),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            sleep=lambda _s: None,
            max_retries=0,
        )
        client._limiter._sleep = lambda _s: None
        return client

    only_steps = [dt.api_id for dt in __import__(
        "fitbit_pipeline.datatypes", fromlist=["DATA_TYPES"]
    ).DATA_TYPES if dt.api_id != "steps"]

    first = Syncer(conn, build_client(), skip_data_types=only_steps)
    result = first.backfill(start=date(2026, 7, 1), end=DAY)
    assert result.status == "failed"

    state = db.get_sync_state(conn, "steps")
    # Two chunks of 7 days each committed before the failure.
    assert state["backfill_cursor"] == "2026-08-04"
    assert not state["backfill_complete"]

    # Resume: the run picks up at the stored cursor rather than at today.
    calls.clear()
    second = Syncer(conn, build_client(), skip_data_types=only_steps)
    second.backfill(start=date(2026, 7, 1), end=DAY)

    first_filter = calls[0].url.params["filter"]
    assert '>= "2026-07-28"' in first_filter and '< "2026-08-04"' in first_filter
    resumed = db.get_sync_state(conn, "steps")
    assert resumed["backfill_complete"] == 1
    assert resumed["backfill_cursor"] == "2026-07-01"


def test_a_completed_backfill_is_not_repeated(conn, fixture_client):
    syncer = Syncer(conn, fixture_client(FIXTURE_PAGES))
    syncer.backfill(start=date(2026, 8, 10), end=DAY)
    calls_before = syncer.client.api_calls

    again = Syncer(conn, fixture_client(FIXTURE_PAGES))
    result = again.backfill(start=date(2026, 8, 10), end=DAY)
    assert again.client.api_calls == 0
    assert result.items == []
    assert calls_before > 0


def test_one_failing_data_type_does_not_sink_the_run(conn, make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/sleep/" in request.url.path:
            return httpx.Response(403, json={"error": {"message": "scope missing"}})
        return httpx.Response(200, json={"dataPoints": []})

    client = make_client(handler, max_retries=0)
    client._limiter._sleep = lambda _s: None
    result = Syncer(conn, client).daily(window_days=1, end=DAY)

    assert result.status == "partial"
    assert [item.data_type for item in result.failures] == ["sleep"]
    run = db.query_one(conn, "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1")
    assert run["status"] == "partial"
    assert "sleep" in run["error"]
    item = db.query_one(
        conn, "SELECT * FROM sync_run_items WHERE data_type = 'sleep' AND run_id = ?", (run["id"],)
    )
    assert item["status"] == "failed"


def test_every_run_is_logged_with_its_counts(conn, fixture_client):
    make_syncer(conn, fixture_client).daily(window_days=1, end=DAY)
    run = db.query_one(conn, "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1")
    assert run["mode"] == "daily"
    assert run["status"] == "ok"
    assert run["api_calls"] > 0
    assert run["records"] > 0
    assert run["raw_pages"] > 0
    assert run["started_at"] and run["finished_at"]


def test_dry_run_writes_nothing(conn, fixture_client):
    syncer = Syncer(conn, fixture_client(FIXTURE_PAGES), dry_run=True)
    result = syncer.daily(window_days=1, end=DAY)

    assert result.records > 0  # it did fetch and normalize
    assert db.query_one(conn, "SELECT COUNT(*) AS n FROM activity_intervals")["n"] == 0
    assert db.query_one(conn, "SELECT COUNT(*) AS n FROM raw_payloads")["n"] == 0
    assert db.query_one(conn, "SELECT COUNT(*) AS n FROM sync_runs")["n"] == 0


def test_backfill_start_comes_from_the_account_creation_date(conn, make_client):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("users/me/profile"):
            return httpx.Response(
                200,
                json={"membershipStartDate": {"year": 2019, "month": 3, "day": 14}, "age": 41},
            )
        return httpx.Response(200, json={"dataPoints": []})

    syncer = Syncer(conn, make_client(handler))
    assert syncer.resolve_backfill_start() == date(2019, 3, 14)
    assert syncer.resolve_backfill_start("2021-01-01") == date(2021, 1, 1)


def test_backfill_start_falls_back_when_the_profile_is_unreadable(conn, make_client):
    client = make_client(lambda request: httpx.Response(403, json={}), max_retries=0)
    client._limiter._sleep = lambda _s: None
    syncer = Syncer(conn, client)
    assert syncer.resolve_backfill_start().year == 2007


def test_broken_credentials_abort_the_run_instead_of_failing_every_type(conn, make_client):
    """A dead refresh token is fatal, not a per data type problem."""
    from fitbit_pipeline.auth import AuthError

    class DeadCredentials:
        account_email = "owner@example.com"

        def token(self):
            raise AuthError("refresh token revoked")

        def refresh(self):
            raise AuthError("refresh token revoked")

    client = make_client(lambda request: httpx.Response(200, json={"dataPoints": []}))
    client.credentials = DeadCredentials()

    with pytest.raises(AuthError, match="revoked"):
        Syncer(conn, client).daily(window_days=1, end=DAY)

    run = db.query_one(conn, "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1")
    assert run["status"] == "failed"
    assert run["error"] == "authorization failed"


def test_ctrl_c_records_what_the_run_achieved(conn, make_client):
    """An interrupted run must not sit in the log as 'running' forever."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] > 2:
            raise KeyboardInterrupt
        return httpx.Response(200, json=load_fixture("steps.json"))

    client = make_client(handler)
    with pytest.raises(KeyboardInterrupt):
        Syncer(conn, client).daily(window_days=1, end=DAY)

    run = db.query_one(conn, "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1")
    assert run["status"] == "interrupted"
    assert run["finished_at"]
    assert run["api_calls"] > 0
    assert "interrupted while fetching" in run["error"]


def test_a_row_left_running_by_a_hard_kill_is_reaped_on_the_next_run(conn, fixture_client):
    orphan = db.start_run(conn, "backfill", "2026-01-01", "2026-08-18")
    assert db.query_one(conn, "SELECT status FROM sync_runs WHERE id = ?", (orphan,))["status"] == "running"

    make_syncer(conn, fixture_client).daily(window_days=1, end=DAY)

    reaped = db.query_one(conn, "SELECT * FROM sync_runs WHERE id = ?", (orphan,))
    assert reaped["status"] == "interrupted"
    assert reaped["finished_at"]


def test_an_interrupted_run_still_summarizes_what_it_fetched(conn, make_client):
    """Killing a backfill must not leave the dashboard showing stale days."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] > 1:
            raise KeyboardInterrupt
        return httpx.Response(200, json=load_fixture("steps.json"))

    client = make_client(handler)
    with pytest.raises(KeyboardInterrupt):
        Syncer(conn, client).daily(window_days=1, end=DAY)

    summary = db.query_one(conn, "SELECT * FROM daily_summary WHERE date = ?", (DAY.isoformat(),))
    assert summary is not None
    assert summary["steps"] == 1592


def test_calories_total_stays_null_when_basal_is_missing(conn):
    """A partial sum must not be presented as a total.

    Google returns basal-energy-burned empty for some accounts. Summing
    active + 0 and labelling it "total" understates the day by a whole BMR
    and silently disagrees with the Fitbit app, so prefer no number.
    """
    from fitbit_pipeline.aggregate import rebuild_daily_summary

    day = DAY.isoformat()
    db.upsert(
        conn,
        "activity_intervals",
        {
            "data_type": "active-energy-burned",
            "start_time": f"{day}T12:00:00Z",
            "source_key": "",
            "subtype": "",
            "end_time": f"{day}T12:01:00Z",
            "local_date": day,
            "value": 456.0,
            "unit": "kcal",
        },
        ("data_type", "start_time", "subtype", "source_key"),
    )
    rebuild_daily_summary(conn, [day])

    row = db.query_one(conn, "SELECT * FROM daily_summary WHERE date = ?", (day,))
    assert row["calories_active_kcal"] == 456.0
    assert row["calories_basal_kcal"] is None
    assert row["calories_total_kcal"] is None


def test_an_unservable_range_is_split_rather_than_abandoned(conn, monkeypatch):
    """A 500 that depends on range width must not strand every older day.

    The backfill walks backward, so abandoning the data type on one bad chunk
    would make all remaining history permanently unreachable.
    """
    import httpx

    from fitbit_pipeline.api import HealthApiClient
    from tests.conftest import FakeCredentials

    seen: list[tuple[str, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # Recover the requested span from the filter the client built.
        import re

        stamps = sorted(set(re.findall(r"\d{4}-\d{2}-\d{2}", str(request.url))))
        span = 0
        if len(stamps) >= 2:
            span = (date.fromisoformat(stamps[-1]) - date.fromisoformat(stamps[0])).days
        reconcile = str(request.url).endswith(":reconcile") or ":reconcile" in str(request.url)
        seen.append(("reconcile" if reconcile else "list", span))
        if reconcile and span > 7:
            return httpx.Response(500, json={"error": {"message": "internal"}})
        return httpx.Response(200, json={"dataPoints": []})

    client = HealthApiClient(
        FakeCredentials(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
        max_retries=0,
    )
    client._limiter._sleep = lambda _s: None

    syncer = Syncer(conn, client)
    dt = BY_ID["active-minutes"]
    # A fourteen day window: too wide to serve, both halves fine.
    result = syncer.fetch_range(dt, date(2023, 3, 14), date(2023, 3, 28))

    spans = [s for _m, s in seen]
    assert 14 in spans, "the wide range should have been attempted first"
    assert 7 in spans, "it should then have been split into halves"
    assert result.data_type == "active-minutes"


def test_a_single_day_reconcile_failure_falls_back_to_list(conn):
    """With nothing left to split, list serves ranges reconcile refuses."""
    import httpx

    from fitbit_pipeline.api import HealthApiClient
    from tests.conftest import FakeCredentials

    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if ":reconcile" in url:
            methods.append("reconcile")
            return httpx.Response(500, json={"error": {"message": "internal"}})
        methods.append("list")
        return httpx.Response(200, json={"dataPoints": []})

    client = HealthApiClient(
        FakeCredentials(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
        max_retries=0,
    )
    client._limiter._sleep = lambda _s: None

    syncer = Syncer(conn, client)
    syncer.fetch_range(BY_ID["active-minutes"], date(2023, 3, 14), date(2023, 3, 15))
    assert "reconcile" in methods and "list" in methods


def test_a_client_error_is_not_split(conn):
    """Splitting is for 5xx. A 400 means the request is wrong at any width."""
    import httpx

    from fitbit_pipeline.api import ApiError, HealthApiClient
    from tests.conftest import FakeCredentials

    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(str(request.url))
        return httpx.Response(400, json={"error": {"message": "bad filter"}})

    client = HealthApiClient(
        FakeCredentials(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
        max_retries=0,
    )
    client._limiter._sleep = lambda _s: None

    syncer = Syncer(conn, client)
    with pytest.raises(ApiError):
        syncer.fetch_range(BY_ID["active-minutes"], date(2023, 3, 1), date(2023, 3, 29))
    assert len(attempts) <= 2, "a 400 must not fan out into a bisect storm"
