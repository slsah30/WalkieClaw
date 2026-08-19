"""Turn API data points into normalized rows.

One function per data type. Each returns a list of Row(table, values, keys),
and the key columns are what make ingestion idempotent: re-running any sync
lands on the same primary key and updates in place.

Canonical API units are preserved. Nothing is converted here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Row:
    table: str
    values: dict[str, Any]
    keys: tuple[str, ...]


# --- shared field helpers ------------------------------------------------


def iso_utc(timestamp: str | None) -> str | None:
    """Normalize an RFC-3339 timestamp to a canonical UTC string.

    Primary keys are built on timestamps, so two spellings of the same instant
    must not create two rows.
    """
    if not timestamp:
        return None
    text = timestamp.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _offset_delta(offset: str | None) -> timedelta:
    """Parse a protobuf Duration string such as '-18000s' into a timedelta."""
    if not offset:
        return timedelta()
    text = str(offset).strip()
    if text.endswith("s"):
        text = text[:-1]
    try:
        return timedelta(seconds=float(text))
    except ValueError:
        return timedelta()


def civil_date(civil: dict[str, Any] | None) -> str | None:
    """Read 'YYYY-MM-DD' out of a CivilDateTime or a Date."""
    if not civil:
        return None
    parts = civil.get("date", civil)
    year, month, day = parts.get("year"), parts.get("month"), parts.get("day")
    if not (year and month and day):
        return None
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def local_date_from(timestamp: str | None, utc_offset: str | None) -> str | None:
    """Fall back to computing the local date when civil time is absent."""
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed.astimezone(timezone.utc) + _offset_delta(utc_offset)).date().isoformat()


def duration_minutes(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        first = datetime.fromisoformat(start.replace("Z", "+00:00"))
        last = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (last - first).total_seconds() / 60.0


def to_number(value: Any) -> float | None:
    """API integers over 32 bits arrive as JSON strings, so coerce carefully."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    number = to_number(value)
    return None if number is None else int(round(number))


def source_key(point: dict[str, Any]) -> str:
    """Stable identifier for the origin of a data point.

    Reconciled points carry no dataSource, since reconciliation is exactly the
    act of merging sources. Those get an empty key, which is what keeps the
    reconciled row and any raw per source rows from colliding.
    """
    data_source = point.get("dataSource")
    if not data_source:
        return ""
    device = data_source.get("device") or {}
    parts = [
        data_source.get("platform", ""),
        data_source.get("recordingMethod", ""),
        device.get("displayName", "") or device.get("formFactor", ""),
    ]
    return "|".join(part for part in parts if part)


def point_id(point: dict[str, Any], fallback: str) -> str:
    """Prefer the API's own identifier for identifiable data types."""
    name = point.get("name") or point.get("dataPointName")
    if name:
        return str(name).rsplit("/", 1)[-1]
    return fallback


def interval_fields(payload: dict[str, Any]) -> dict[str, Any]:
    interval = payload.get("interval") or {}
    start = iso_utc(interval.get("startTime"))
    end = iso_utc(interval.get("endTime"))
    local = civil_date(interval.get("civilStartTime")) or local_date_from(
        interval.get("startTime"), interval.get("startUtcOffset")
    )
    return {
        "start_time": start,
        "end_time": end,
        "local_date": local,
        "start_utc_offset": interval.get("startUtcOffset"),
    }


def sample_fields(payload: dict[str, Any]) -> dict[str, Any]:
    sample = payload.get("sampleTime") or {}
    physical = iso_utc(sample.get("physicalTime"))
    local = civil_date(sample.get("civilTime")) or local_date_from(
        sample.get("physicalTime"), sample.get("utcOffset")
    )
    return {
        "sample_time": physical,
        "local_date": local,
        "utc_offset": sample.get("utcOffset"),
    }


# --- interval shaped activity metrics ------------------------------------

ACTIVITY_KEYS = ("data_type", "start_time", "subtype", "source_key")


def _activity_row(
    data_type_id: str,
    point: dict[str, Any],
    payload: dict[str, Any],
    value: float | None,
    unit: str,
    subtype: str = "",
) -> Row | None:
    fields = interval_fields(payload)
    if not fields["start_time"] or not fields["local_date"]:
        return None
    return Row(
        "activity_intervals",
        {
            "data_type": data_type_id,
            "start_time": fields["start_time"],
            "source_key": source_key(point),
            "subtype": subtype,
            "end_time": fields["end_time"],
            "local_date": fields["local_date"],
            "start_utc_offset": fields["start_utc_offset"],
            "value": value,
            "unit": unit,
        },
        ACTIVITY_KEYS,
    )


def _simple_interval(data_type_id: str, field: str, unit: str) -> Callable[..., list[Row]]:
    def normalizer(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
        row = _activity_row(data_type_id, point, payload, to_number(payload.get(field)), unit)
        return [row] if row else []

    return normalizer


def _duration_interval(data_type_id: str, subtype_field: str | None) -> Callable[..., list[Row]]:
    """Types whose value is the length of the interval itself."""

    def normalizer(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
        interval = payload.get("interval") or {}
        minutes = duration_minutes(interval.get("startTime"), interval.get("endTime"))
        subtype = str(payload.get(subtype_field) or "") if subtype_field else ""
        row = _activity_row(data_type_id, point, payload, minutes, "minutes", subtype)
        return [row] if row else []

    return normalizer


def normalize_active_zone_minutes(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
    row = _activity_row(
        "active-zone-minutes",
        point,
        payload,
        to_number(payload.get("activeZoneMinutes")),
        "azm",
        str(payload.get("heartRateZone") or ""),
    )
    return [row] if row else []


def normalize_active_minutes(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
    rows = []
    for bucket in payload.get("activeMinutesByActivityLevel") or []:
        row = _activity_row(
            "active-minutes",
            point,
            payload,
            to_number(bucket.get("activeMinutes")),
            "minutes",
            str(bucket.get("activityLevel") or ""),
        )
        if row:
            rows.append(row)
    return rows


# --- heart rate -----------------------------------------------------------


def normalize_heart_rate(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
    fields = sample_fields(payload)
    bpm = to_int(payload.get("beatsPerMinute"))
    if not fields["sample_time"] or bpm is None or not fields["local_date"]:
        return []
    metadata = payload.get("metadata") or {}
    return [
        Row(
            "heartrate_intraday",
            {
                "sample_time": fields["sample_time"],
                "source_key": source_key(point),
                "local_date": fields["local_date"],
                "bpm": bpm,
                "motion_context": metadata.get("motionContext"),
                "sensor_location": metadata.get("sensorLocation"),
                "utc_offset": fields["utc_offset"],
            },
            ("sample_time", "source_key"),
        )
    ]


def normalize_resting_hr(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
    day = civil_date(payload.get("date"))
    bpm = to_int(payload.get("beatsPerMinute"))
    if not day or bpm is None:
        return []
    metadata = payload.get("dailyRestingHeartRateMetadata") or {}
    return [
        Row(
            "resting_hr",
            {
                "date": day,
                "bpm": bpm,
                "calculation_method": metadata.get("calculationMethod"),
            },
            ("date",),
        )
    ]


def normalize_hr_zones(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
    day = civil_date(payload.get("date"))
    if not day:
        return []
    rows = []
    for zone in payload.get("heartRateZones") or []:
        rows.append(
            Row(
                "hr_zones_daily",
                {
                    "date": day,
                    "zone": str(zone.get("heartRateZoneType") or ""),
                    "min_bpm": to_int(zone.get("minBeatsPerMinute")),
                    "max_bpm": to_int(zone.get("maxBeatsPerMinute")),
                },
                ("date", "zone"),
            )
        )
    return rows


# --- sleep ----------------------------------------------------------------

STAGE_COLUMNS = {
    "DEEP": "minutes_deep",
    "LIGHT": "minutes_light",
    "REM": "minutes_rem",
    "RESTLESS": "minutes_restless",
}


def normalize_sleep(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
    interval = payload.get("interval") or {}
    start = iso_utc(interval.get("startTime"))
    end = iso_utc(interval.get("endTime"))
    if not start or not end:
        return []

    # A night is reported on the day the sleeper woke up, which is why the API
    # filters sleep on end time and why this uses the civil end date.
    local = civil_date(interval.get("civilEndTime")) or local_date_from(
        interval.get("endTime"), interval.get("endUtcOffset")
    )
    if not local:
        return []

    session_id = point_id(point, f"sleep:{start}")
    metadata = payload.get("metadata") or {}
    summary = payload.get("summary") or {}

    session: dict[str, Any] = {
        "session_id": session_id,
        "start_time": start,
        "end_time": end,
        "local_date": local,
        "start_utc_offset": interval.get("startUtcOffset"),
        "end_utc_offset": interval.get("endUtcOffset"),
        "sleep_type": payload.get("type"),
        "is_main_sleep": 1 if metadata.get("mainSleep") else 0,
        "is_nap": 1 if metadata.get("nap") else 0,
        "processed": 1 if metadata.get("processed") else 0,
        "manually_edited": 1 if metadata.get("manuallyEdited") else 0,
        "stages_status": metadata.get("stagesStatus"),
        "external_id": metadata.get("externalId"),
        "minutes_asleep": to_number(summary.get("minutesAsleep")),
        "minutes_awake": to_number(summary.get("minutesAwake")),
        "minutes_in_period": to_number(summary.get("minutesInSleepPeriod")),
        "minutes_to_fall_asleep": to_number(summary.get("minutesToFallAsleep")),
        "minutes_after_wakeup": to_number(summary.get("minutesAfterWakeUp")),
        "minutes_deep": None,
        "minutes_light": None,
        "minutes_rem": None,
        "minutes_restless": None,
        "source_key": source_key(point),
    }
    for stage_summary in summary.get("stagesSummary") or []:
        column = STAGE_COLUMNS.get(str(stage_summary.get("type")))
        if column:
            session[column] = to_number(stage_summary.get("minutes"))

    rows = [Row("sleep_sessions", session, ("session_id",))]

    def stage_rows(stages: list[dict[str, Any]], short: bool) -> None:
        for stage in stages or []:
            stage_start = iso_utc(stage.get("startTime"))
            stage_end = iso_utc(stage.get("endTime"))
            if not stage_start or not stage_end:
                continue
            minutes = duration_minutes(stage.get("startTime"), stage.get("endTime")) or 0.0
            rows.append(
                Row(
                    "sleep_stages",
                    {
                        "session_id": session_id,
                        "start_time": stage_start,
                        "stage": str(stage.get("type") or ""),
                        "end_time": stage_end,
                        "duration_seconds": minutes * 60.0,
                        "is_short_awakening": 1 if short else 0,
                    },
                    ("session_id", "start_time", "stage"),
                )
            )

    stage_rows(payload.get("stages") or [], short=False)
    stage_rows(payload.get("shortAwakenings") or [], short=True)

    # When the API gives stages but no summary, derive the per stage minutes so
    # the sleep report is never blank for an otherwise complete night.
    if session["minutes_deep"] is None and payload.get("stages"):
        totals: dict[str, float] = {}
        for stage in payload["stages"]:
            minutes = duration_minutes(stage.get("startTime"), stage.get("endTime")) or 0.0
            totals[str(stage.get("type"))] = totals.get(str(stage.get("type")), 0.0) + minutes
        for stage_type, column in STAGE_COLUMNS.items():
            if stage_type in totals:
                session[column] = totals[stage_type]
        if session["minutes_awake"] is None and "AWAKE" in totals:
            session["minutes_awake"] = totals["AWAKE"]
        if session["minutes_asleep"] is None:
            session["minutes_asleep"] = sum(
                minutes for stage_type, minutes in totals.items() if stage_type != "AWAKE"
            )
        if session["minutes_in_period"] is None:
            session["minutes_in_period"] = duration_minutes(
                interval.get("startTime"), interval.get("endTime")
            )
    return rows


# --- health metrics -------------------------------------------------------


def normalize_hrv(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
    fields = sample_fields(payload)
    if not fields["sample_time"] or not fields["local_date"]:
        return []
    return [
        Row(
            "hrv",
            {
                "sample_time": fields["sample_time"],
                "source_key": source_key(point),
                "local_date": fields["local_date"],
                "rmssd_ms": to_number(
                    payload.get("rootMeanSquareOfSuccessiveDifferencesMilliseconds")
                ),
                "sdnn_ms": to_number(payload.get("standardDeviationMilliseconds")),
            },
            ("sample_time", "source_key"),
        )
    ]


def normalize_hrv_daily(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
    day = civil_date(payload.get("date"))
    if not day:
        return []
    return [
        Row(
            "hrv_daily",
            {
                "date": day,
                "avg_rmssd_ms": to_number(payload.get("averageHeartRateVariabilityMilliseconds")),
                "deep_sleep_rmssd_ms": to_number(
                    payload.get("deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds")
                ),
                "entropy": to_number(payload.get("entropy")),
                "non_rem_hr_bpm": to_number(payload.get("nonRemHeartRateBeatsPerMinute")),
            },
            ("date",),
        )
    ]


def normalize_spo2(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
    fields = sample_fields(payload)
    percentage = to_number(payload.get("percentage"))
    if not fields["sample_time"] or percentage is None or not fields["local_date"]:
        return []
    return [
        Row(
            "spo2",
            {
                "sample_time": fields["sample_time"],
                "source_key": source_key(point),
                "local_date": fields["local_date"],
                "percentage": percentage,
            },
            ("sample_time", "source_key"),
        )
    ]


def normalize_spo2_daily(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
    day = civil_date(payload.get("date"))
    if not day:
        return []
    return [
        Row(
            "spo2_daily",
            {
                "date": day,
                "average_percent": to_number(payload.get("averagePercentage")),
                "lower_percent": to_number(payload.get("lowerBoundPercentage")),
                "upper_percent": to_number(payload.get("upperBoundPercentage")),
                "stddev_percent": to_number(payload.get("standardDeviationPercentage")),
            },
            ("date",),
        )
    ]


def normalize_breathing_rate_daily(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
    day = civil_date(payload.get("date"))
    rate = to_number(payload.get("breathsPerMinute"))
    if not day or rate is None:
        return []
    return [Row("breathing_rate_daily", {"date": day, "breaths_per_minute": rate}, ("date",))]


def normalize_breathing_rate_sleep(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
    fields = sample_fields(payload)
    if not fields["sample_time"] or not fields["local_date"]:
        return []

    def stat(key: str, field: str = "breathsPerMinute") -> float | None:
        return to_number((payload.get(key) or {}).get(field))

    return [
        Row(
            "breathing_rate_sleep",
            {
                "sample_time": fields["sample_time"],
                "source_key": source_key(point),
                "local_date": fields["local_date"],
                "full_bpm": stat("fullSleepStats"),
                "deep_bpm": stat("deepSleepStats"),
                "light_bpm": stat("lightSleepStats"),
                "rem_bpm": stat("remSleepStats"),
                "full_stddev": stat("fullSleepStats", "standardDeviation"),
            },
            ("sample_time", "source_key"),
        )
    ]


def normalize_skin_temp(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
    day = civil_date(payload.get("date"))
    if not day:
        return []
    return [
        Row(
            "skin_temp",
            {
                "date": day,
                "nightly_celsius": to_number(payload.get("nightlyTemperatureCelsius")),
                "baseline_celsius": to_number(payload.get("baselineTemperatureCelsius")),
                "relative_stddev_30d_celsius": to_number(
                    payload.get("relativeNightlyStddev30dCelsius")
                ),
            },
            ("date",),
        )
    ]


def _sample_measure(table: str, field: str, column: str) -> Callable[..., list[Row]]:
    def normalizer(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
        fields = sample_fields(payload)
        value = to_number(payload.get(field))
        if not fields["sample_time"] or value is None or not fields["local_date"]:
            return []
        row = {
            "sample_time": fields["sample_time"],
            "source_key": source_key(point),
            "local_date": fields["local_date"],
            column: value,
        }
        if table == "weight":
            row["notes"] = payload.get("notes")
        return [Row(table, row, ("sample_time", "source_key"))]

    return normalizer


def normalize_vo2max_daily(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
    day = civil_date(payload.get("date"))
    if not day:
        return []
    return [
        Row(
            "vo2max_daily",
            {
                "date": day,
                "vo2_max": to_number(payload.get("vo2Max")),
                "cardio_fitness_level": payload.get("cardioFitnessLevel"),
                "estimated": 1 if payload.get("estimated") else 0,
            },
            ("date",),
        )
    ]


# --- sessions -------------------------------------------------------------


def normalize_exercise(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
    fields = interval_fields(payload)
    if not fields["start_time"] or not fields["local_date"]:
        return []
    summary = payload.get("metricsSummary") or {}
    metadata = payload.get("exerciseMetadata") or {}
    interval = payload.get("interval") or {}
    duration = duration_minutes(interval.get("startTime"), interval.get("endTime"))
    active = payload.get("activeDuration")
    active_seconds = None
    if isinstance(active, str) and active.endswith("s"):
        active_seconds = to_number(active[:-1])

    return [
        Row(
            "workouts",
            {
                "exercise_id": point_id(point, f"exercise:{fields['start_time']}"),
                "start_time": fields["start_time"],
                "end_time": fields["end_time"],
                "local_date": fields["local_date"],
                "start_utc_offset": fields["start_utc_offset"],
                "exercise_type": payload.get("exerciseType"),
                "display_name": payload.get("displayName"),
                "duration_seconds": None if duration is None else duration * 60.0,
                "active_duration_seconds": active_seconds,
                "distance_mm": to_number(summary.get("distanceMillimeters")),
                "calories_kcal": to_number(summary.get("caloriesKcal")),
                "steps": to_int(summary.get("steps")),
                "average_hr_bpm": to_int(summary.get("averageHeartRateBeatsPerMinute")),
                "active_zone_minutes": to_int(summary.get("activeZoneMinutes")),
                "elevation_gain_mm": to_number(summary.get("elevationGainMillimeters")),
                "average_speed_mm_s": to_number(summary.get("averageSpeedMillimetersPerSecond")),
                "has_gps": 1 if metadata.get("hasGps") else 0,
                "notes": payload.get("notes"),
                "source_key": source_key(point),
            },
            ("exercise_id",),
        )
    ]


def normalize_ecg(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
    fields = interval_fields(payload)
    if not fields["start_time"]:
        return []
    local = fields["local_date"] or (fields["start_time"] or "")[:10]
    return [
        Row(
            "ecg_readings",
            {
                "reading_id": point_id(point, f"ecg:{fields['start_time']}"),
                "start_time": fields["start_time"],
                "end_time": fields["end_time"],
                "local_date": local,
                "result_classification": payload.get("resultClassification"),
                "average_bpm": to_int(payload.get("beatsPerMinuteAvg")),
                "sampling_frequency_hz": to_int(payload.get("samplingFrequencyHertz")),
                "lead_number": to_int(payload.get("leadNumber")),
            },
            ("reading_id",),
        )
    ]


def normalize_irn(point: dict[str, Any], payload: dict[str, Any]) -> list[Row]:
    fields = interval_fields(payload)
    if not fields["start_time"]:
        return []
    local = fields["local_date"] or (fields["start_time"] or "")[:10]
    return [
        Row(
            "irn_events",
            {
                "event_id": point_id(point, f"irn:{fields['start_time']}"),
                "start_time": fields["start_time"],
                "end_time": fields["end_time"],
                "local_date": local,
                "window_count": len(payload.get("alertWindows") or []),
            },
            ("event_id",),
        )
    ]


NORMALIZERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], list[Row]]] = {
    "steps": _simple_interval("steps", "count", "count"),
    "distance": _simple_interval("distance", "millimeters", "mm"),
    "floors": _simple_interval("floors", "count", "count"),
    "active-energy-burned": _simple_interval("active-energy-burned", "kcal", "kcal"),
    "basal-energy-burned": _simple_interval("basal-energy-burned", "kcal", "kcal"),
    "active-zone-minutes": normalize_active_zone_minutes,
    "active-minutes": normalize_active_minutes,
    "activity-level": _duration_interval("activity-level", "activityLevelType"),
    "sedentary-period": _duration_interval("sedentary-period", None),
    "time-in-heart-rate-zone": _duration_interval("time-in-heart-rate-zone", "heartRateZoneType"),
    "heart-rate": normalize_heart_rate,
    "daily-resting-heart-rate": normalize_resting_hr,
    "daily-heart-rate-zones": normalize_hr_zones,
    "sleep": normalize_sleep,
    "heart-rate-variability": normalize_hrv,
    "daily-heart-rate-variability": normalize_hrv_daily,
    "oxygen-saturation": normalize_spo2,
    "daily-oxygen-saturation": normalize_spo2_daily,
    "daily-respiratory-rate": normalize_breathing_rate_daily,
    "respiratory-rate-sleep-summary": normalize_breathing_rate_sleep,
    "daily-sleep-temperature-derivations": normalize_skin_temp,
    "weight": _sample_measure("weight", "weightGrams", "weight_grams"),
    "body-fat": _sample_measure("body_fat", "percentage", "percentage"),
    "height": _sample_measure("height", "heightMillimeters", "height_millimeters"),
    "daily-vo2-max": normalize_vo2max_daily,
    "exercise": normalize_exercise,
    "electrocardiogram": normalize_ecg,
    "irregular-rhythm-notification": normalize_irn,
}


def normalize_point(data_type_id: str, payload_key: str, point: dict[str, Any]) -> list[Row]:
    """Extract normalized rows from one data point, or nothing if it is empty."""
    payload = point.get(payload_key)
    if not isinstance(payload, dict):
        return []
    normalizer = NORMALIZERS.get(data_type_id)
    if normalizer is None:
        log.debug("no normalizer registered", extra={"data_type": data_type_id})
        return []
    try:
        return normalizer(point, payload)
    except Exception:  # a malformed point must not abort a whole sync
        log.exception("could not normalize point", extra={"data_type": data_type_id})
        return []
