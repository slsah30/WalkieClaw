"""Registry of Google Health API v4 data types this pipeline ingests.

Every field here was read out of the v4 discovery document, revision 20260817,
vendored at docs/health-v4-discovery.json. See docs/api-findings.md for the
mapping from each PRD metric to the entries below.

The time model is the important part. The API rejects a filter that uses the
wrong time field for a data type, so the model drives filter construction:

    interval  {prefix}.interval.civil_start_time
    sample    {prefix}.sample_time.civil_time
    daily     {prefix}.date
    session   {prefix}.interval.civil_start_time
    sleep     sleep.interval.civil_end_time      (a night belongs to the morning)
    ecg       electrocardiogram.interval.start_time, lower bound only
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# Scope groups map onto the readonly scopes requested at consent.
SCOPE_ACTIVITY = "activity_and_fitness"
SCOPE_SLEEP = "sleep"
SCOPE_METRICS = "health_metrics_and_measurements"
SCOPE_ECG = "ecg"
SCOPE_IRN = "irn"

# Optional groups are only ingested when the matching alias is listed in
# config.auth.extra_scopes, because each one widens the consent screen.
OPTIONAL_SCOPE_GROUPS = {SCOPE_ECG: "ecg", SCOPE_IRN: "irn"}


@dataclass(frozen=True)
class DataType:
    """One data type in the API and how to read and store it."""

    api_id: str
    """Path segment, kebab-case: users/me/dataTypes/{api_id}."""

    payload_key: str
    """camelCase key carrying the payload inside a DataPoint."""

    time_model: str
    """One of: interval, sample, daily, session, sleep, ecg."""

    scope_group: str
    table: str
    description: str
    page_size: int = 1000
    chunk_days: int = 30
    supports_reconcile: bool = True
    subtypes: tuple[str, ...] = field(default=())
    """Field name whose value distinguishes rows sharing a start time."""

    @property
    def filter_prefix(self) -> str:
        return self.api_id.replace("-", "_")

    @property
    def parent(self) -> str:
        return f"users/me/dataTypes/{self.api_id}"

    def build_filter(self, start: date, end: date) -> str:
        """Half open filter over [start, end): start inclusive, end exclusive."""
        prefix = self.filter_prefix
        if self.time_model == "daily":
            return (
                f'{prefix}.date >= "{start.isoformat()}" AND '
                f'{prefix}.date < "{end.isoformat()}"'
            )
        if self.time_model == "sample":
            field_name = f"{prefix}.sample_time.civil_time"
        elif self.time_model == "sleep":
            field_name = "sleep.interval.civil_end_time"
        elif self.time_model == "ecg":
            # ECG supports only a lower bound, and only physical time.
            return (
                f'electrocardiogram.interval.start_time >= '
                f'"{start.isoformat()}T00:00:00Z"'
            )
        else:  # interval and session
            field_name = f"{prefix}.interval.civil_start_time"
        return (
            f'{field_name} >= "{start.isoformat()}" AND '
            f'{field_name} < "{end.isoformat()}"'
        )


# Page size notes from discovery: default 1440, maximum 10000, except exercise
# and sleep where both default and maximum are 25.
DATA_TYPES: tuple[DataType, ...] = (
    # Activity and fitness.
    DataType(
        api_id="steps",
        payload_key="steps",
        time_model="interval",
        scope_group=SCOPE_ACTIVITY,
        table="activity_intervals",
        description="Step count per interval",
        page_size=10000,
        chunk_days=7,
    ),
    DataType(
        api_id="distance",
        payload_key="distance",
        time_model="interval",
        scope_group=SCOPE_ACTIVITY,
        table="activity_intervals",
        description="Distance in millimeters per interval",
        page_size=10000,
        chunk_days=7,
    ),
    DataType(
        api_id="floors",
        payload_key="floors",
        time_model="interval",
        scope_group=SCOPE_ACTIVITY,
        table="activity_intervals",
        description="Floors climbed per interval",
        page_size=10000,
        chunk_days=7,
    ),
    DataType(
        api_id="active-zone-minutes",
        payload_key="activeZoneMinutes",
        time_model="interval",
        scope_group=SCOPE_ACTIVITY,
        table="activity_intervals",
        description="Active zone minutes, tagged with the heart rate zone",
        page_size=10000,
        chunk_days=14,
        subtypes=("heartRateZone",),
    ),
    DataType(
        api_id="active-energy-burned",
        payload_key="activeEnergyBurned",
        time_model="interval",
        scope_group=SCOPE_ACTIVITY,
        table="activity_intervals",
        description="Calories burned by activity, excluding basal",
        page_size=10000,
        chunk_days=7,
    ),
    DataType(
        api_id="basal-energy-burned",
        payload_key="basalEnergyBurned",
        time_model="interval",
        scope_group=SCOPE_ACTIVITY,
        table="activity_intervals",
        description="Calories burned at basal metabolic rate",
        page_size=10000,
        chunk_days=14,
    ),
    DataType(
        api_id="active-minutes",
        payload_key="activeMinutes",
        time_model="interval",
        scope_group=SCOPE_ACTIVITY,
        table="activity_intervals",
        description="Active minutes bucketed by activity level",
        page_size=10000,
        chunk_days=14,
        subtypes=("activityLevel",),
    ),
    DataType(
        api_id="activity-level",
        payload_key="activityLevel",
        time_model="interval",
        scope_group=SCOPE_ACTIVITY,
        table="activity_intervals",
        description="Sedentary through very active intervals",
        page_size=10000,
        chunk_days=7,
        subtypes=("activityLevelType",),
    ),
    DataType(
        api_id="sedentary-period",
        payload_key="sedentaryPeriod",
        time_model="interval",
        scope_group=SCOPE_ACTIVITY,
        table="activity_intervals",
        description="Periods spent not moving while wearing the device",
        page_size=10000,
        chunk_days=14,
    ),
    DataType(
        api_id="time-in-heart-rate-zone",
        payload_key="timeInHeartRateZone",
        time_model="interval",
        scope_group=SCOPE_ACTIVITY,
        table="activity_intervals",
        description="Time spent in each heart rate zone",
        page_size=10000,
        chunk_days=14,
        subtypes=("heartRateZoneType",),
    ),
    DataType(
        api_id="heart-rate",
        payload_key="heartRate",
        time_model="sample",
        scope_group=SCOPE_ACTIVITY,
        table="heartrate_intraday",
        description="Intraday heart rate samples at device granularity",
        page_size=10000,
        chunk_days=1,
    ),
    DataType(
        api_id="daily-resting-heart-rate",
        payload_key="dailyRestingHeartRate",
        time_model="daily",
        scope_group=SCOPE_ACTIVITY,
        table="resting_hr",
        description="Daily resting heart rate",
        chunk_days=90,
    ),
    DataType(
        api_id="daily-heart-rate-zones",
        payload_key="dailyHeartRateZones",
        time_model="daily",
        scope_group=SCOPE_ACTIVITY,
        table="hr_zones_daily",
        description="Karvonen heart rate zone thresholds per day",
        chunk_days=90,
    ),
    DataType(
        api_id="exercise",
        payload_key="exercise",
        time_model="session",
        scope_group=SCOPE_ACTIVITY,
        table="workouts",
        description="Exercise and workout sessions",
        page_size=25,
        chunk_days=30,
    ),
    DataType(
        api_id="daily-vo2-max",
        payload_key="dailyVo2Max",
        time_model="daily",
        scope_group=SCOPE_ACTIVITY,
        table="vo2max_daily",
        description="Cardio fitness score",
        chunk_days=90,
    ),
    # Sleep.
    DataType(
        api_id="sleep",
        payload_key="sleep",
        time_model="sleep",
        scope_group=SCOPE_SLEEP,
        table="sleep_sessions",
        description="Sleep sessions with stages and summary",
        page_size=25,
        chunk_days=14,
    ),
    # Health metrics and measurements.
    DataType(
        api_id="heart-rate-variability",
        payload_key="heartRateVariability",
        time_model="sample",
        scope_group=SCOPE_METRICS,
        table="hrv",
        description="HRV samples, RMSSD and SDNN",
        page_size=10000,
        chunk_days=7,
    ),
    DataType(
        api_id="daily-heart-rate-variability",
        payload_key="dailyHeartRateVariability",
        time_model="daily",
        scope_group=SCOPE_METRICS,
        table="hrv_daily",
        description="Daily HRV summary",
        chunk_days=90,
    ),
    DataType(
        api_id="oxygen-saturation",
        payload_key="oxygenSaturation",
        time_model="sample",
        scope_group=SCOPE_METRICS,
        table="spo2",
        description="SpO2 samples",
        page_size=10000,
        chunk_days=7,
    ),
    DataType(
        api_id="daily-oxygen-saturation",
        payload_key="dailyOxygenSaturation",
        time_model="daily",
        scope_group=SCOPE_METRICS,
        table="spo2_daily",
        description="Daily SpO2 average and confidence bounds",
        chunk_days=90,
    ),
    DataType(
        api_id="daily-respiratory-rate",
        payload_key="dailyRespiratoryRate",
        time_model="daily",
        scope_group=SCOPE_METRICS,
        table="breathing_rate_daily",
        description="Daily breathing rate from main sleep",
        chunk_days=90,
    ),
    DataType(
        api_id="respiratory-rate-sleep-summary",
        payload_key="respiratoryRateSleepSummary",
        time_model="sample",
        scope_group=SCOPE_METRICS,
        table="breathing_rate_sleep",
        description="Breathing rate broken down by sleep stage",
        chunk_days=30,
    ),
    DataType(
        api_id="daily-sleep-temperature-derivations",
        payload_key="dailySleepTemperatureDerivations",
        time_model="daily",
        scope_group=SCOPE_METRICS,
        table="skin_temp",
        description="Nightly skin temperature and its 30 day baseline",
        chunk_days=90,
    ),
    DataType(
        api_id="weight",
        payload_key="weight",
        time_model="sample",
        scope_group=SCOPE_METRICS,
        table="weight",
        description="Body weight measurements",
        chunk_days=90,
    ),
    DataType(
        api_id="body-fat",
        payload_key="bodyFat",
        time_model="sample",
        scope_group=SCOPE_METRICS,
        table="body_fat",
        description="Body fat percentage measurements",
        chunk_days=90,
    ),
    DataType(
        api_id="height",
        payload_key="height",
        time_model="sample",
        scope_group=SCOPE_METRICS,
        table="height",
        description="Height measurements",
        chunk_days=365,
    ),
    # Opt in, each needs its own restricted scope.
    DataType(
        api_id="electrocardiogram",
        payload_key="electrocardiogram",
        time_model="ecg",
        scope_group=SCOPE_ECG,
        table="ecg_readings",
        description="ECG readings",
        page_size=25,
        chunk_days=365,
        supports_reconcile=False,
    ),
    DataType(
        api_id="irregular-rhythm-notification",
        payload_key="irregularRhythmNotification",
        time_model="session",
        scope_group=SCOPE_IRN,
        table="irn_events",
        description="Irregular rhythm notifications",
        page_size=25,
        chunk_days=365,
        supports_reconcile=False,
    ),
)

BY_ID: dict[str, DataType] = {dt.api_id: dt for dt in DATA_TYPES}


def enabled_data_types(
    extra_scopes: list[str] | None = None,
    skip: list[str] | None = None,
) -> list[DataType]:
    """Data types to ingest given the granted scopes and the skip list."""
    granted_optional = {alias.strip().lower() for alias in (extra_scopes or [])}
    skipped = {item.strip() for item in (skip or [])}
    selected = []
    for data_type in DATA_TYPES:
        alias = OPTIONAL_SCOPE_GROUPS.get(data_type.scope_group)
        if alias and alias not in granted_optional:
            continue
        if data_type.api_id in skipped:
            continue
        selected.append(data_type)
    return selected


def resolve(api_id: str) -> DataType:
    try:
        return BY_ID[api_id]
    except KeyError:
        known = ", ".join(sorted(BY_ID))
        raise KeyError(f"unknown data type {api_id!r}. Known: {known}") from None
