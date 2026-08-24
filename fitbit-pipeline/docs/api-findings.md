# Phase 0 findings: Google Health API v4

Source of truth for this document is the live API discovery document, fetched
2026-08-19 and vendored at [`health-v4-discovery.json`](./health-v4-discovery.json).

- Discovery revision: `20260817`
- API version: `v4`
- Base URL: `https://health.googleapis.com/`
- Legacy Fitbit Web API turndown: 2026-09-30. Nothing in this project talks to it.

Everything below that is marked **(discovery)** was read out of that document, so
it is exact. Items marked **(secondary)** come from Google guides and third party
migration write ups that were reachable from this build environment, and should be
re-checked at first live auth.

---

## 1. Request shape

All health data lives under one uniform resource:

```
users/{user}/dataTypes/{data_type}/dataPoints
```

`{user}` is `me` for a personal integration. `{data_type}` is the kebab-case data
type id, for example `users/me/dataTypes/steps`.

Four read methods exist **(discovery)**:

| Method | HTTP | Path |
| --- | --- | --- |
| `list` | GET | `v4/users/me/dataTypes/{dt}/dataPoints` |
| `reconcile` | GET | `v4/users/me/dataTypes/{dt}/dataPoints:reconcile` |
| `rollUp` | POST | `v4/users/me/dataTypes/{dt}/dataPoints:rollUp` |
| `dailyRollUp` | POST | `v4/users/me/dataTypes/{dt}/dataPoints:dailyRollUp` |

This pipeline reads with **`reconcile`** wherever the data type supports it, which
is what FR-2 asks for: reconcile merges overlapping data points from multiple
sources into one stream so stored values match what the Fitbit app shows. It falls
back to `list` automatically when the API rejects `reconcile` for a data type.

`rollUp` and `dailyRollUp` are server side aggregations. This pipeline does not use
them for ingestion, because storing raw and reconciled points and aggregating in
SQL keeps every derived number reproducible from the archive. `dailyRollUp` remains
useful for spot checking against the Fitbit app, and the client exposes it.

### Reconciled stream selection **(discovery)**

`reconcile` takes a `dataSourceFamily` query parameter, formatted
`users/me/dataSourceFamilies/{family}`:

- `all-sources` (default): every available data source.
- `google-wearables`: Google and Fitbit tracker devices such as Fitbit trackers and
  Pixel Watch. Excludes manually logged data.
- `google-sources`: first party Google data, including trackers, manually logged
  data, and Health Connect.

Default in `config.toml` is `all-sources`.

### Time filters **(discovery)**

The `filter` query parameter follows [AIP-160](https://google.aip.dev/160). The
supported field depends on the data type's time model. This is the single most
important detail for the ingestion code, because using the wrong field returns an
error rather than an empty result:

| Time model | Filter pattern | Literal format |
| --- | --- | --- |
| Interval | `{type}.interval.start_time` | RFC-3339 |
| Interval, civil | `{type}.interval.civil_start_time` | `YYYY-MM-DD[THH:mm:ss]` |
| Sample | `{type}.sample_time.physical_time` | RFC-3339 |
| Sample, civil | `{type}.sample_time.civil_time` | `YYYY-MM-DD[THH:mm:ss]` |
| Daily summary | `{type}.date` | `YYYY-MM-DD` |
| Session, not sleep or ECG | `{type}.interval.civil_start_time` | `YYYY-MM-DD[THH:mm:ss]` |
| Sleep | `sleep.interval.end_time` or `sleep.interval.civil_end_time` | RFC-3339 / ISO |
| ECG | `electrocardiogram.interval.start_time`, `>=` only | RFC-3339 |

The `{type}` prefix is the data type id in snake_case, so `active_zone_minutes`,
`daily_resting_heart_rate`, and so on. Operators are `>=` and `<`; the logical
operator is `AND` (`OR` additionally allowed for sleep). Results come back ordered
by interval start time descending.

Sleep is filtered on **end** time, which is exactly right for a sleep pipeline: a
night that starts at 23:40 belongs to the following morning's report.

### Pagination and page size **(discovery)**

`pageSize` defaults to 1440 and caps at 10000, **except** `exercise` and `sleep`,
where both the default and the maximum are 25. `nextPageToken` drives paging. The
client encodes these per-type caps so it never sends an over-large page size.

---

## 2. OAuth scopes **(discovery)**

Every Google Health scope is `https://www.googleapis.com/auth/googlehealth.{name}`.
The readonly scopes that exist:

`profile.readonly`, `settings.readonly`, `activity_and_fitness.readonly`,
`sleep.readonly`, `health_metrics_and_measurements.readonly`, `ecg.readonly`,
`irn.readonly`, `location.readonly`.

Minimum set this project requests for FR-2:

```
https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly
https://www.googleapis.com/auth/googlehealth.sleep.readonly
https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly
https://www.googleapis.com/auth/googlehealth.settings.readonly
https://www.googleapis.com/auth/googlehealth.profile.readonly
openid
https://www.googleapis.com/auth/userinfo.email
```

`settings.readonly` is needed to read the account's unit and timezone preferences,
`profile.readonly` to read `membershipStartDate`, which is what bounds the backfill.
`openid` plus `userinfo.email` are what make the Workspace account precondition
checkable before any health call is made (see section 5).

Optional, opt in through `config.toml` because they widen consent:

- `ecg.readonly` for `electrocardiogram`
- `irn.readonly` for `irregular-rhythm-notification`
- `location.readonly` for exercise GPS and TCX export

No write scope is ever requested. This is a read only pipeline (Non-Goal 2).

---

## 3. Metric coverage for a Fitbit Charge 6

Every PRD metric mapped to a real data type id. Time model drives the filter field;
scope drives consent.

| PRD metric | Data type id | Time model | Scope | Notes |
| --- | --- | --- | --- | --- |
| Steps | `steps` | interval | activity | Intraday buckets, `count` |
| Distance | `distance` | interval | activity | `millimeters` |
| Floors | `floors` | interval | activity | `count` |
| Active zone minutes | `active-zone-minutes` | interval | activity | Carries `heartRateZone` of `FAT_BURN`, `CARDIO`, `PEAK`; value is 1 per fat burn minute and 2 per cardio or peak minute |
| Calories, activity | `active-energy-burned` | interval | activity | `kcal` |
| Calories, basal | `basal-energy-burned` | interval | activity | `kcal` |
| Active minutes | `active-minutes` | interval | activity | Bucketed `LIGHT` / `MODERATE` / `VIGOROUS` |
| Activity level | `activity-level` | interval | activity | `SEDENTARY` … `VERY_ACTIVE` |
| Sedentary time | `sedentary-period` | interval | activity | Interval only |
| Time in HR zone | `time-in-heart-rate-zone` | interval | activity | `LIGHT` / `MODERATE` / `VIGOROUS` / `PEAK` |
| HR zone thresholds | `daily-heart-rate-zones` | daily | activity | Karvonen thresholds per day |
| Heart rate, intraday | `heart-rate` | sample | activity | `beatsPerMinute` per sample, plus `motionContext` and `sensorLocation` |
| Resting heart rate | `daily-resting-heart-rate` | daily | activity | Plus `calculationMethod` |
| HRV, intraday | `heart-rate-variability` | sample | metrics | RMSSD and SDNN in ms |
| HRV, daily | `daily-heart-rate-variability` | daily | metrics | Average RMSSD, deep sleep RMSSD, entropy, non-REM HR |
| Sleep | `sleep` | session (end time) | sleep | Session with `stages[]`, `summary`, `metadata.mainSleep`, `metadata.nap` |
| SpO2, intraday | `oxygen-saturation` | sample | metrics | `percentage` |
| SpO2, daily | `daily-oxygen-saturation` | daily | metrics | Average plus confidence bounds |
| Breathing rate | `daily-respiratory-rate` | daily | metrics | `breathsPerMinute`, main sleep |
| Breathing rate by stage | `respiratory-rate-sleep-summary` | sample | metrics | Full, deep, light, REM statistics |
| Skin temperature variation | `daily-sleep-temperature-derivations` | daily | metrics | Nightly value, 30 day baseline, 30 day stddev, all Celsius |
| Workouts | `exercise` | session (civil start) | activity | 190 exercise types, splits, metrics summary, TCX export |
| Weight | `weight` | sample | metrics | `weightGrams` |
| Body fat | `body-fat` | sample | metrics | `percentage` |
| Height | `height` | sample | metrics | Logged rarely |
| Cardio fitness | `daily-vo2-max` | daily | activity | VO2 max plus `cardioFitnessLevel` |
| ECG | `electrocardiogram` | session, `>=` only | ecg (opt in) | `list` only |
| Irregular rhythm | `irregular-rhythm-notification` | session | irn (opt in) | `list` only |

### Metrics that do not exist in the API

- **Stress and EDA.** There is no stress score, stress management score, or EDA
  data type anywhere in the v4 `DataPoint` union **(discovery)**. The closest
  adjacent types are `moods` and `symptoms`, which are manual logs, not the Charge 6
  EDA sensor. PRD 5.2 flagged this as conditional. Recording it as unavailable and
  continuing, per Phase 0 instructions.
- **Sleep score (the 0 to 100 number).** Not exposed **(discovery)**. See open
  question 5.
- **Breathing rate as an intraday series.** Only the daily value and the per stage
  sleep summary exist.

Also present in the API and ingested because a Charge 6 account may carry them:
`vo2-max`, `run-vo2-max`, `blood-glucose` and nutrition types are deliberately
skipped (no Charge 6 sensor, and nutrition would need a wider scope).

---

## 4. Rate limits and retries

Google documents that the Health API enforces daily, per minute, and per user
quotas and answers `429 Too Many Requests` on breach, but the exact numeric limits
are not published in the discovery document and were not reachable from this build
environment **(secondary)**. The pipeline therefore does not hardcode a number:

- A client side token bucket, `sync.max_requests_per_minute`, default 60.
- Exponential backoff with jitter on 429, 500, 502, 503, and 504.
- `Retry-After` is honored when the response carries it.
- Backfill checkpoints after every committed chunk, so hitting a daily quota just
  stops the run and the next run resumes at the same cursor.

Re-check the live numbers at <https://developers.google.com/health/rate-limits>
once authorized, and set `max_requests_per_minute` from them.

---

## 5. Open questions from PRD section 9

### 1. Is intraday granularity available to a personal, single user app?

**Yes, with no separate application.** In the legacy Fitbit Web API, intraday
access was gated behind the "Personal" app type or a manual request form. In v4 the
gate is gone: `heart-rate`, `heart-rate-variability`, and `oxygen-saturation` are
ordinary sample data types read through the same `list` and `reconcile` methods as
everything else, under `activity_and_fitness.readonly` and
`health_metrics_and_measurements.readonly` **(discovery)**. The `pageSize` default
of 1440, which is one point per minute for a day, is a strong tell that per minute
data is the expected shape.

Granularity is whatever the device recorded. The pipeline stores every sample
returned and never downsamples, satisfying the acceptance criterion.

### 2. Exact scope strings and consent screen requirements

Scope strings are in section 2 above, verbatim from discovery. Consent screen: every
`googlehealth.*` scope is classified **Restricted**, so an app that wants to serve
users other than its owner has to pass Google's verification and, for restricted
scopes, a third party security assessment **(secondary)**. A single user personal
app never gets there, and does not need to: the owner can consent to their own
unverified app by clicking through the "Google hasn't verified this app" interstitial
via **Advanced** and then **Go to (app name) (unsafe)**.

### 3. Refresh token lifetime under Testing mode

**The 7 day expiry is real and it does apply here.** A refresh token issued while
the OAuth consent screen's publishing status is **Testing** expires after 7 days,
unless the app requests only basic profile scopes **(secondary)**. Health scopes are
restricted, so Testing mode would break acceptance criterion 1 ("30+ days later,
daily sync still runs without human interaction") on day 8.

The usual escape, setting user type to **Internal**, is unavailable: Internal
requires a Google Workspace organization, and the Health API does not support
Workspace accounts at all (section 6). Service accounts are also out, because there
is no domain wide delegation path to a personal Google account's health data.

**Correction, confirmed against a live consent attempt on 2026-08-24.** An earlier
draft of this document concluded that publishing the app to "In production" and
leaving it unverified would yield non expiring refresh tokens. That is wrong for
restricted scopes. An unverified production app requesting `googlehealth.*` scopes
does not reach the consent screen at all; Google returns:

> Access blocked: (app name) has not completed the Google verification process

with no **Advanced** / **unsafe** click through. The interstitial that can be
clicked through only appears for an app in **Testing**. So the two states are:

| Publishing status | Restricted scope consent | Refresh token lifetime |
| --- | --- | --- |
| Testing, requester is a test user | Works, after the unverified interstitial | 7 days |
| In production, unverified | Hard blocked | n/a |
| In production, verified | Works | Long lived |

Reaching the third row requires Google verification plus a third party CASA
security assessment. That is not reachable for a single user personal project.

**Chosen configuration: Testing, with the owner added as a test user, and the 7 day
re-auth accepted as an operational cost.** This does mean acceptance criterion 1
("30+ days later, daily sync still runs without human interaction") cannot be met as
written against the Health API today. The honest restatement is that the sync runs
unattended for up to 7 days at a time, and needs one browser consent per week.

Google's Health API documentation describes a **personal use exception** to
restricted scope verification (<https://developers.google.com/health/app-verification>).
Whether that exception can be exercised to get a long lived token for a single user
app was not resolvable from this environment and is the open item worth chasing
before treating the weekly re-auth as permanent.

Mitigations built in, because this is now a routine event rather than a failure:

- `fitbit-sync doctor` reports the age of the stored refresh token and warns when it
  approaches 7 days.
- An `invalid_grant` on refresh is caught and re-raised with the exact remedy text
  rather than a stack trace.

### 4. How far back does history go, and is Google Takeout needed?

`users/me/profile` exposes `membershipStartDate`, the date the account was created
**(discovery)**, and the backfill uses it as the lower bound rather than guessing.
Nothing in the discovery document caps historical reads, and the reconciled stream
is documented as covering the account's data sources including
`FITBIT_WEB_API`-sourced points **(discovery, via `DataSource.platform`)**, which
suggests pre migration history is reachable.

**Answered on 2026-08-24 by a full backfill against a live account.** No Takeout
importer is needed. History reaches back to the account creation date, across the
Fitbit web API era and the migration to Google, with no gap at the boundary.

The run covered 3,936 days and 10.7 million records in about four hours, and
produced a 4.6 GB database. The earliest date that actually returned data, per
type:

| Reaches account creation, 2015-11-15 | First available |
| --- | --- |
| steps, distance, floors, activity-level, active-minutes | 2015-11-15 |
| heart-rate (intraday), time-in-heart-rate-zone, daily-heart-rate-zones | 2015-11-15 |
| exercise, weight, height | 2015-11-15 |
| daily-resting-heart-rate | 2015-11-17 |
| sleep | 2015-11-16 |
| sedentary-period | 2016-03-04 |

| Starts at a later device, not an API limit | First available |
| --- | --- |
| active-zone-minutes | 2022-12-25 |
| oxygen-saturation, daily-oxygen-saturation | 2022-12-26 |
| heart-rate-variability and its daily rollup | 2022-12-30 |
| daily-sleep-temperature-derivations, respiratory-rate-sleep-summary | 2022-12-30 |
| daily-respiratory-rate | 2022-12-31 |

The 2022-12 cluster is the date the user's first sensor capable device arrived,
not a retention boundary. Three types returned nothing at any date on this
account: `basal-energy-burned`, `daily-vo2-max`, and `body-fat`. The first two
have consequences for reporting and are noted in the README.

**Eleven years of intraday heart rate is real and it is large.** 8.1 million
samples, roughly one every two seconds while worn. That dominates the database
size; skipping `heart-rate` via `sync.skip_data_types` cuts it by about 90 percent
if daily aggregates are enough.

**The reconcile endpoint fails on some ranges by width, not by content.** Two
fourteen day chunks returned 500 no matter how often they were retried, while both
seven day halves, every individual day, and the same fourteen days on `:list` all
succeeded. Because the backfill walks backward, one such chunk would strand every
older day for that data type. `fetch_range` therefore halves a range on a 5xx and
falls back to `:list` at a single day. Expect this: it hit twice in 11,600 calls,
about one chunk in 5,800.

### 5. Is sleep score exposed?

**No.** There is no sleep score field on `Sleep`, `SleepSummary`, or `SleepMetadata`,
and no sleep score data type **(discovery)**. What the API does give per session:
`minutesAsleep`, `minutesAwake`, `minutesInSleepPeriod`, `minutesToFallAsleep`,
`minutesAfterWakeUp`, and a per stage `stagesSummary`, plus `daily-heart-rate-variability`,
`daily-respiratory-rate`, `daily-resting-heart-rate` and
`daily-sleep-temperature-derivations` for the same night.

The dashboard reports sleep efficiency, computed as
`minutesAsleep / minutesInSleepPeriod`, and labels it as such. It does not print a
0 to 100 number dressed up as the Fitbit sleep score, because approximating that
number without Google's coefficients would produce something that quietly disagrees
with the app.

---

## 6. Preconditions verified at first auth

Both PRD preconditions are checked by `fitbit-sync auth` and `fitbit-sync doctor`
before any data is written:

1. **Not a Workspace account.** Google Workspace accounts cannot be linked to Google
   Health, and cannot be used to migrate a Fitbit account **(secondary)**. Detection
   does not need a health call: the OIDC ID token carries an `hd` (hosted domain)
   claim only for Workspace accounts. If `hd` is present, auth aborts with a message
   naming the domain and telling the owner to re-run with a personal account.
2. **Fitbit account migrated to Google sign in.** If the account is not migrated,
   `users/me/identity` fails or returns no `legacyUserId`. Auth calls it and reports
   the outcome, including the Fitbit user id, which is a useful cross reference
   against legacy exports.

---

## Sources

- Live discovery document, `https://health.googleapis.com/$discovery/rest?version=v4`, revision 20260817
- [About the Google Health API](https://developers.google.com/health/about)
- [Google Health API REST reference](https://developers.google.com/health/reference/rest)
- [Google Health API data types](https://developers.google.com/health/data-types)
- [Quotas and rate limits](https://developers.google.com/health/rate-limits)
- [Using OAuth 2.0 to Access Google APIs](https://developers.google.com/identity/protocols/oauth2)
- [Move your Fitbit account to a Google Account](https://support.google.com/googlehealth/answer/14237024)
- [Fitbit to Google Health API developer transition guide, Validic](https://help.validic.com/space/VCS/5513478151/Fitbit+to+Google+Health+API+Developer+Transition+Guide)
- [How the new Google Health API works, Terra](https://tryterra.co/blog/everything-you-need-to-know-about-google-health-new-api)
