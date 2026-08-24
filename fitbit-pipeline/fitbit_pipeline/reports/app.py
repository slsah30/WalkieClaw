"""The local dashboard.

FastAPI plus Jinja2 templates, rendering charts as inline SVG. The choice is
argued in the README: a server rendered dashboard needs no JavaScript bundle,
no CDN, and no build step, which keeps the "no cloud dependency beyond the API
itself" requirement literally true and makes every view unit testable.

Binds to 127.0.0.1 unless server.expose_lan is set.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from fitbit_pipeline import units
from fitbit_pipeline.config import Config, load_config
from fitbit_pipeline.reports import charts, queries

TEMPLATES = Path(__file__).parent / "templates"

STAGE_COLORS = [
    ("Deep", "minutes_deep", "var(--stage-deep)"),
    ("REM", "minutes_rem", "var(--stage-rem)"),
    ("Light", "minutes_light", "var(--stage-light)"),
    ("Awake", "minutes_awake", "var(--stage-awake)"),
]


def _fmt(value: Any, digits: int = 0, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:,.{digits}f}{suffix}"


def _display(metric: str, value: float | None) -> tuple[str, str]:
    """Convert a stored value to its imperial display form and label."""
    unit = queries.METRICS[metric]["unit"]
    if value is None:
        return "n/a", ""
    if unit == "mm":
        return _fmt(units.mm_to_miles(value), 2), "mi"
    if unit == "grams":
        return _fmt(units.grams_to_pounds(value), 1), "lb"
    if unit == "celsius_delta":
        return _fmt(units.celsius_delta_to_fahrenheit(value), 2), "F"
    if unit == "minutes":
        return _fmt(value, 0), "min"
    if unit == "percent":
        return _fmt(value, 1), "%"
    if unit in {"bpm", "ms", "breaths/min"}:
        return _fmt(value, 1), unit
    return _fmt(value, 0), unit


def _display_value(metric: str, value: float | None) -> float | None:
    """Same conversion, but numeric, for charts and CSV."""
    if value is None:
        return None
    unit = queries.METRICS[metric]["unit"]
    if unit == "mm":
        return units.mm_to_miles(value)
    if unit == "grams":
        return units.grams_to_pounds(value)
    if unit == "celsius_delta":
        return units.celsius_delta_to_fahrenheit(value)
    return float(value)


def _display_unit(metric: str) -> str:
    unit = queries.METRICS[metric]["unit"]
    return {
        "mm": "mi",
        "grams": "lb",
        "celsius_delta": "F",
        "percent": "%",
        "minutes": "min",
    }.get(unit, unit)


def csv_response(filename: str, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def parse_day(value: str | None, conn: sqlite3.Connection) -> str:
    if value:
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            raise HTTPException(400, f"Not a date: {value}") from None
    return queries.latest_day_with_data(conn) or date.today().isoformat()


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    app = FastAPI(title="Fitbit pipeline", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATES))
    templates.env.filters["fmt"] = _fmt
    templates.env.filters["miles"] = lambda mm: _fmt(units.mm_to_miles(mm), 2)
    templates.env.filters["feet"] = lambda mm: _fmt(units.mm_to_feet(mm), 0)
    templates.env.filters["pounds"] = lambda grams: _fmt(units.grams_to_pounds(grams), 1)
    templates.env.filters["fahrenheit"] = lambda c: _fmt(units.celsius_to_fahrenheit(c), 1)
    templates.env.filters["delta_f"] = lambda c: _fmt(units.celsius_delta_to_fahrenheit(c), 2)
    templates.env.filters["hhmm"] = units.minutes_to_hhmm
    templates.env.filters["local_clock"] = queries.local_clock
    templates.env.filters["clock"] = _clock
    app.state.config = config

    def get_conn() -> Any:
        connection = sqlite3.connect(f"file:{config.database.path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def page(request: Request, template: str, **context: Any) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name=template,
            context={"metrics": queries.METRICS, **context},
        )

    # --- view 1: daily summary -------------------------------------------

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse("/day")

    @app.get("/day", response_class=HTMLResponse)
    def day_view(
        request: Request,
        date_: str | None = Query(None, alias="date"),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        day = parse_day(date_, conn)
        summary = queries.daily_summary(conn, day)
        previous, following = queries.neighbouring_days(conn, day)
        sessions = queries.sleep_sessions_for(conn, day)
        stages = queries.sleep_stages_for(conn, sessions[0]["session_id"]) if sessions else []
        samples = queries.heartrate_samples(conn, day)

        # Stage minutes live on the sleep session. daily_summary carries copies
        # under sleep_ prefixed names, used when no session row survived.
        stage_source = sessions[0] if sessions else {}
        stage_stack = [
            (name, stage_source[column] or 0.0, color)
            for name, column, color in STAGE_COLORS
            if stage_source.get(column)
        ]
        if not stage_stack and summary:
            stage_stack = [
                (name, summary[f"sleep_{column}"] or 0.0, color)
                for name, column, color in STAGE_COLORS
                if summary.get(f"sleep_{column}")
            ]

        return page(
            request,
            "day.html",
            day=day,
            summary=summary,
            previous=previous,
            following=following,
            sessions=sessions,
            stages=stages,
            workouts=queries.workouts_for(conn, day),
            azm=queries.azm_breakdown(conn, day),
            hr_chart=charts.intraday_heartrate_chart(
                _hr_points(samples), queries.heartrate_zones(conn, day), height=240
            ),
            sleep_chart=(
                charts.stacked_bar_chart(
                    [day], [stage_stack], height=200, y_label="Sleep minutes"
                )
                if stage_stack
                else charts.empty_chart("No sleep stages recorded", 200)
            ),
            granularity=queries.heartrate_granularity(samples),
            span=queries.database_span(conn),
        )

    # --- view 2: trends ---------------------------------------------------

    @app.get("/trends", response_class=HTMLResponse)
    def trends_view(
        request: Request,
        days: int = Query(90, ge=7, le=1095),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        end = queries.latest_day_with_data(conn) or date.today().isoformat()
        start = (date.fromisoformat(end) - timedelta(days=days - 1)).isoformat()

        panels = []
        for metric in ("resting_hr", "hrv", "sleep_minutes", "steps", "weight"):
            raw = queries.metric_series(conn, metric, start, end)
            converted = [(day, _display_value(metric, value)) for day, value in raw]
            series = [charts.Series("Daily", converted, "var(--series-muted)", width=1.2)]
            for window, color in ((7, "var(--series-1)"), (30, "var(--series-2)"), (90, "var(--series-3)")):
                if days >= window:
                    series.append(
                        charts.Series(
                            f"{window} day average",
                            queries.rolling_average(converted, window),
                            color,
                        )
                    )
            panels.append(
                {
                    "metric": metric,
                    "label": queries.METRICS[metric]["label"],
                    "unit": _display_unit(metric),
                    "chart": charts.line_chart(
                        series,
                        y_label=queries.METRICS[metric]["label"],
                        value_format=lambda value: f"{value:,.0f}",
                    ),
                    "summary": queries.trend_summary(converted),
                }
            )

        return page(
            request,
            "trends.html",
            days=days,
            day_options=sorted({30, 90, 180, 365, 730, days}),
            start=start,
            end=end,
            panels=panels,
            span=queries.database_span(conn),
        )

    # --- view 3: sleep ----------------------------------------------------

    @app.get("/sleep", response_class=HTMLResponse)
    def sleep_view(
        request: Request,
        days: int = Query(30, ge=7, le=365),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        end = queries.latest_day_with_data(conn) or date.today().isoformat()
        start = (date.fromisoformat(end) - timedelta(days=days - 1)).isoformat()
        nights = queries.sleep_nights(conn, start, end)

        categories = [night["local_date"][5:] for night in nights]
        stacks = [
            [
                (name, night[column] or 0.0, color)
                for name, column, color in STAGE_COLORS
                if night.get(column)
            ]
            for night in nights
        ]
        consistency = queries.sleep_consistency(nights)
        scatter_points = [
            (
                row["bedtime"],
                row["waketime"],
                f"{row['date']}: to bed {_clock(row['bedtime'])}, up {_clock(row['waketime'])}",
            )
            for row in consistency
            if row["bedtime"] is not None and row["waketime"] is not None
        ]

        return page(
            request,
            "sleep.html",
            days=days,
            day_options=sorted({14, 30, 60, 90, 180, days}),
            start=start,
            end=end,
            nights=list(reversed(nights)),
            stage_chart=charts.stacked_bar_chart(
                categories, stacks, y_label="Minutes", height=320
            ),
            consistency_chart=charts.scatter_chart(
                scatter_points,
                x_label="Bedtime (local clock)",
                y_label="Wake time (local clock)",
                x_format=_clock,
                y_format=_clock,
                height=320,
            ),
            weekly=queries.sleep_weekly_averages(conn, start, end),
            span=queries.database_span(conn),
        )

    # --- view 4: intraday heart rate --------------------------------------

    @app.get("/heartrate", response_class=HTMLResponse)
    def heartrate_view(
        request: Request,
        date_: str | None = Query(None, alias="date"),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        day = parse_day(date_, conn)
        samples = queries.heartrate_samples(conn, day)
        zones = queries.heartrate_zones(conn, day)
        previous, following = queries.neighbouring_days(conn, day)
        bpms = [row["bpm"] for row in samples]

        return page(
            request,
            "heartrate.html",
            day=day,
            previous=previous,
            following=following,
            chart=charts.intraday_heartrate_chart(_hr_points(samples), zones, height=380),
            zones=zones,
            sample_count=len(samples),
            granularity=queries.heartrate_granularity(samples),
            stats={
                "min": min(bpms) if bpms else None,
                "max": max(bpms) if bpms else None,
                "avg": sum(bpms) / len(bpms) if bpms else None,
            },
            span=queries.database_span(conn),
        )

    # --- view 5: correlations ---------------------------------------------

    @app.get("/correlations", response_class=HTMLResponse)
    def correlations_view(
        request: Request,
        x: str = Query("steps"),
        y: str = Query("sleep_minutes"),
        lag: int = Query(1, ge=0, le=7),
        days: int = Query(180, ge=14, le=1095),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        if x not in queries.METRICS or y not in queries.METRICS:
            raise HTTPException(400, "Unknown metric")
        end = queries.latest_day_with_data(conn) or date.today().isoformat()
        start = (date.fromisoformat(end) - timedelta(days=days - 1)).isoformat()
        rows = queries.correlation_pairs(conn, x, y, start, end, lag)

        pairs = [
            (_display_value(x, row["x"]), _display_value(y, row["y"]))
            for row in rows
            if row["x"] is not None and row["y"] is not None
        ]
        pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
        points = [
            (a, b, f"{row['date']} against {row['y_date']}")
            for (a, b), row in zip(pairs, rows)
        ]

        return page(
            request,
            "correlations.html",
            x=x,
            y=y,
            lag=lag,
            days=days,
            day_options=sorted({30, 90, 180, 365, 730, days}),
            start=start,
            end=end,
            count=len(pairs),
            r=queries.pearson(pairs),
            chart=charts.scatter_chart(
                points,
                x_label=f"{queries.METRICS[x]['label']} ({_display_unit(x)})",
                y_label=(
                    f"{queries.METRICS[y]['label']} ({_display_unit(y)})"
                    + (f", {lag} day later" if lag else "")
                ),
                fit=queries.linear_fit(pairs),
            ),
            span=queries.database_span(conn),
        )

    # --- status -----------------------------------------------------------

    @app.get("/status", response_class=HTMLResponse)
    def status_view(
        request: Request, conn: sqlite3.Connection = Depends(get_conn)
    ) -> HTMLResponse:
        return page(
            request,
            "status.html",
            states=queries.sync_status(conn),
            runs=queries.recent_runs(conn, 15),
            span=queries.database_span(conn),
        )

    # --- CSV export --------------------------------------------------------

    @app.get("/export/daily.csv")
    def export_daily(
        start: str | None = None,
        end: str | None = None,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Response:
        lo, hi = queries.database_span(conn)
        rows = conn.execute(
            "SELECT * FROM daily_summary WHERE date BETWEEN ? AND ? ORDER BY date",
            (start or lo or "0000-01-01", end or hi or "9999-12-31"),
        ).fetchall()
        if not rows:
            return csv_response("daily_summary.csv", ["date"], [])
        header = list(rows[0].keys())
        return csv_response("daily_summary.csv", header, [[row[k] for k in header] for row in rows])

    @app.get("/export/sleep.csv")
    def export_sleep(
        start: str | None = None,
        end: str | None = None,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Response:
        lo, hi = queries.database_span(conn)
        rows = conn.execute(
            "SELECT * FROM sleep_sessions WHERE local_date BETWEEN ? AND ? ORDER BY local_date",
            (start or lo or "0000-01-01", end or hi or "9999-12-31"),
        ).fetchall()
        if not rows:
            return csv_response("sleep.csv", ["local_date"], [])
        header = list(rows[0].keys())
        return csv_response("sleep.csv", header, [[row[k] for k in header] for row in rows])

    @app.get("/export/heartrate.csv")
    def export_heartrate(
        date_: str | None = Query(None, alias="date"),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Response:
        day = parse_day(date_, conn)
        rows = queries.heartrate_samples(conn, day)
        return csv_response(
            f"heartrate_{day}.csv",
            ["sample_time", "bpm", "motion_context"],
            [[row["sample_time"], row["bpm"], row["motion_context"]] for row in rows],
        )

    @app.get("/export/workouts.csv")
    def export_workouts(conn: sqlite3.Connection = Depends(get_conn)) -> Response:
        rows = conn.execute("SELECT * FROM workouts ORDER BY start_time").fetchall()
        if not rows:
            return csv_response("workouts.csv", ["start_time"], [])
        header = list(rows[0].keys())
        return csv_response("workouts.csv", header, [[row[k] for k in header] for row in rows])

    @app.get("/export/trends.csv")
    def export_trends(
        metric: str = Query("resting_hr"),
        days: int = Query(90, ge=7, le=1095),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Response:
        if metric not in queries.METRICS:
            raise HTTPException(400, "Unknown metric")
        end = queries.latest_day_with_data(conn) or date.today().isoformat()
        start = (date.fromisoformat(end) - timedelta(days=days - 1)).isoformat()
        raw = queries.metric_series(conn, metric, start, end)
        converted = [(day, _display_value(metric, value)) for day, value in raw]
        avg7 = dict(queries.rolling_average(converted, 7))
        avg30 = dict(queries.rolling_average(converted, 30))
        unit = _display_unit(metric)
        return csv_response(
            f"trend_{metric}.csv",
            ["date", f"{metric}_{unit}", "avg_7d", "avg_30d"],
            [[day, value, avg7.get(day), avg30.get(day)] for day, value in converted],
        )

    @app.get("/export/correlations.csv")
    def export_correlations(
        x: str = Query("steps"),
        y: str = Query("sleep_minutes"),
        lag: int = Query(1, ge=0, le=7),
        days: int = Query(180, ge=14, le=1095),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> Response:
        if x not in queries.METRICS or y not in queries.METRICS:
            raise HTTPException(400, "Unknown metric")
        end = queries.latest_day_with_data(conn) or date.today().isoformat()
        start = (date.fromisoformat(end) - timedelta(days=days - 1)).isoformat()
        rows = queries.correlation_pairs(conn, x, y, start, end, lag)
        return csv_response(
            f"correlation_{x}_vs_{y}_lag{lag}.csv",
            ["date", f"{x}_{_display_unit(x)}", "y_date", f"{y}_{_display_unit(y)}"],
            [
                [row["date"], _display_value(x, row["x"]), row["y_date"], _display_value(y, row["y"])]
                for row in rows
            ],
        )

    return app


def _clock(position: float | None) -> str:
    """Render a clock position from queries.clock_position back as HH:MM."""
    if position is None:
        return "n/a"
    hours = position % 24
    return f"{int(hours):02d}:{int(round((hours % 1) * 60)) % 60:02d}"


def _hr_points(samples: Sequence[dict[str, Any]]) -> list[tuple[float, int, str]]:
    """Convert stored UTC samples into hours since local midnight."""
    points = []
    for row in samples:
        try:
            moment = datetime.fromisoformat(str(row["sample_time"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        offset = str(row.get("utc_offset") or "0s")
        if offset.endswith("s"):
            offset = offset[:-1]
        try:
            local = moment + timedelta(seconds=float(offset))
        except ValueError:
            local = moment
        hours = local.hour + local.minute / 60.0 + local.second / 3600.0
        points.append((hours, int(row["bpm"]), f"{local.strftime('%H:%M')}  {row['bpm']} bpm"))
    return sorted(points)
