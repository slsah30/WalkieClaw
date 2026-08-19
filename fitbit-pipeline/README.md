# fitbit-pipeline

A single user pipeline that pulls a Fitbit Charge 6's data from the **Google
Health API v4**, stores it in SQLite, and serves local reports. No cloud
services beyond the API itself, no third party accounts, no telemetry.

The legacy Fitbit Web API shuts down on 2026-09-30. Nothing here talks to it.

- Ingestion targets `https://health.googleapis.com/v4`, using the reconciled
  stream so stored values match what the Fitbit app shows.
- Storage is one SQLite file: raw API responses plus normalized tables.
- Reports are rendered server side as HTML with inline SVG charts, so the
  dashboard works with no JavaScript bundle and no network access of its own.
- After one browser consent, the daily sync runs unattended.

Full API notes, including the metric to endpoint mapping and the answers to the
open questions from the spec, are in [`docs/api-findings.md`](docs/api-findings.md).

---

## Quick start

```bash
git clone <this repo>
cd fitbit-pipeline

python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .

cp config.example.toml config.toml     # defaults are fine to start
# ... complete the Google Cloud setup below, then:

fitbit-sync auth        # one browser consent
fitbit-sync doctor      # confirms preconditions and prints your account details
fitbit-sync daily       # last 3 days, proves the pipeline works
fitbit-sync backfill    # full history, resumable, takes a while
fitbit-sync serve       # dashboard on http://127.0.0.1:8722
```

Requires Python 3.12 or newer.

---

## Google Cloud setup, click by click

You need a Google Cloud project with the Health API enabled and an OAuth client.
This takes about ten minutes and costs nothing.

### 1. Confirm the preconditions

- Your Fitbit account must already be **migrated to Google sign in**. Check by
  signing in to the Fitbit app with your Google account.
- The account must be a **personal Google account**, not a Google Workspace
  account. Workspace accounts cannot be linked to Google Health at all, and
  cannot be used to migrate a Fitbit account. `fitbit-sync auth` refuses to
  continue if it sees a Workspace account, so you find out immediately rather
  than through a confusing error later.

### 2. Create the project

1. Open <https://console.cloud.google.com/projectcreate>.
2. Sign in with the **same personal Google account** your Fitbit data lives on.
3. Project name: anything, for example `fitbit-pipeline`. Leave Location as
   "No organization".
4. Click **Create**, then wait for the notification and select the new project
   in the picker at the top of the page.

### 3. Enable the Health API

1. Go to **APIs and services**, then **Library**, or open
   <https://console.cloud.google.com/apis/library>.
2. Search for `Health API`. Pick the one whose service name is
   `health.googleapis.com`.
3. Click **Enable**.

### 4. Configure the OAuth consent screen

1. Go to **APIs and services**, then **OAuth consent screen**.
2. User type: **External**. Click **Create**. (Internal is only offered to
   Workspace organizations, which cannot use this API anyway.)
3. App name: anything, for example `fitbit-pipeline`. Set the user support email
   and developer contact email to your own address. Click **Save and continue**.
4. Scopes: you can click **Save and continue** without adding any. The scopes are
   requested by the app at authorization time. If you prefer to list them, they
   are printed by `fitbit-sync doctor` and documented in `docs/api-findings.md`.
5. Test users: add your own Google address. Click **Save and continue**.

### 5. Publish the app, which is what keeps the refresh token alive

**Do not skip this.** While the publishing status is **Testing**, Google expires
every refresh token after **7 days**, and the daily sync would stop working after
a week.

1. Still on **OAuth consent screen**, find **Publishing status**.
2. Click **Publish app**, then confirm.
3. The status becomes **In production**. You will see a note about verification.
   Ignore it. Verification is only required to serve *other people*; an unverified
   production app works for its owner, and refresh tokens issued by a production
   app do not expire on a schedule.

The one visible consequence is an "Google hasn't verified this app" warning at
the single consent screen in step 7. That is expected.

### 6. Create the OAuth client

1. Go to **APIs and services**, then **Credentials**.
2. **Create credentials**, then **OAuth client ID**.
3. Application type: **Desktop app**. Name it anything. Click **Create**.
4. In the dialog, click **Download JSON**.
5. Save that file as `secrets/client_secret.json` inside this repo:

   ```bash
   mkdir -p secrets && chmod 700 secrets
   mv ~/Downloads/client_secret_*.json secrets/client_secret.json
   chmod 600 secrets/client_secret.json
   ```

   `secrets/` is in `.gitignore` from the first commit. Nothing in it is ever
   committed.

### 7. Authorize

```bash
fitbit-sync auth
```

A browser opens. Sign in with the personal Google account, click **Advanced**
then **Go to (your app name) (unsafe)** past the unverified app warning, and
approve the read only permissions.

The refresh token is written to `secrets/token.json` with mode 0600 inside a 0700
directory. This is the only interactive step. Every later run refreshes silently.

Confirm it worked:

```bash
fitbit-sync doctor
```

That reports file permissions, granted scopes, the refresh token's age, whether
the account is personal rather than Workspace, whether the Fitbit account is
migrated (it prints your legacy Fitbit user id), your account timezone, the
account creation date, and your paired devices.

---

## Using it

### Ingestion

```bash
fitbit-sync daily                  # re-read the last 3 days (config.sync.window_days)
fitbit-sync daily --days 7         # wider window, for example after a trip
fitbit-sync daily --dry-run        # fetch one day, print what would be written, write nothing
fitbit-sync backfill               # walk back to the account creation date
fitbit-sync backfill --since 2024-01-01
fitbit-sync backfill --restart     # ignore stored checkpoints
fitbit-sync daily --data-type sleep --data-type heart-rate
fitbit-sync status                 # per data type: coverage, backfill progress, record counts
fitbit-sync rebuild                # re-derive daily_summary from the normalized tables
```

The daily window matters. A sleep session posts to the API after you wake up, a
watch that has not synced backdates its data, and both mean yesterday's numbers
are still changing. Re-reading a few days is safe because every write is an
idempotent upsert keyed on `(data_type, timestamp, source)`. Running any sync
twice produces zero duplicate rows.

**Backfill is resumable.** It walks backward from today in per type chunks and
checkpoints after every committed chunk. Kill it with Ctrl-C, lose power, hit a
daily quota: the next run picks up from the stored cursor rather than starting
over. `fitbit-sync status` shows where each data type stopped.

### Dashboard

```bash
fitbit-sync serve
```

Then open <http://127.0.0.1:8722>. Five views:

1. **Daily summary.** Any day: steps, distance, floors, calories, active zone
   minutes, resting heart rate, HRV, sleep and stages, SpO2, breathing rate,
   skin temperature variation, weight, workouts.
2. **Trends.** 7, 30, and 90 day rolling averages for resting heart rate, HRV,
   sleep duration, steps, and weight, over a window you choose.
3. **Sleep.** Stage breakdown per night, bedtime and wake consistency, weekly
   averages, and a table of every night.
4. **Intraday heart rate.** Every sample the API returned for a chosen day, with
   heart rate zone shading. Nothing is downsampled, and the page states the
   actual sample spacing it found.
5. **Correlations.** Any two daily metrics against each other with an optional
   lag, for example prior day steps against tonight's sleep, or sleep against
   the next day's HRV. Shows Pearson r and a least squares fit.

There is also a **Status** view showing sync coverage and recent runs.

Every view has a CSV button. The database is plain SQLite, so ad hoc analysis
needs nothing special:

```bash
sqlite3 data/fitbit.sqlite3 \
  "SELECT date, steps, resting_hr, sleep_minutes_asleep FROM daily_summary ORDER BY date DESC LIMIT 14;"
```

The dashboard binds to `127.0.0.1` and has no authentication. To reach it from
other machines on your LAN, set `expose_lan = true` under `[server]`, which binds
`0.0.0.0`. Do not port forward it.

### Units

Reports display imperial: miles, pounds, degrees Fahrenheit. The database stores
exactly what the API returns, which is millimeters, grams, and Celsius. Nothing
is converted on the way in, so re-deriving a number in other units is always
possible.

---

## Scheduling

### systemd, recommended

```bash
sudo cp deploy/fitbit-sync.service deploy/fitbit-sync.timer /etc/systemd/system/
sudoedit /etc/systemd/system/fitbit-sync.service   # set User, WorkingDirectory, ExecStart
sudo systemctl daemon-reload
sudo systemctl enable --now fitbit-sync.timer

systemctl list-timers fitbit-sync.timer
journalctl -u fitbit-sync.service -n 50
```

The timer fires at 06:30 with a randomized delay, and `Persistent=true` catches
up after the machine was off. `deploy/fitbit-sync.dashboard.service` optionally
keeps the dashboard running.

### cron

See `deploy/crontab.example`. Cron runs with a minimal environment, so it sets
`FITBIT_SYNC_CONFIG` explicitly and uses absolute paths.

### Docker, optional

```bash
docker build -t fitbit-pipeline .
docker run --rm \
  -v "$PWD/data:/app/data" -v "$PWD/secrets:/app/secrets" \
  -v "$PWD/config.toml:/app/config.toml:ro" \
  fitbit-pipeline daily
```

Run `fitbit-sync auth` on the host first; the container reuses the mounted token.

---

## Configuration

`config.toml`, documented inline in `config.example.toml`. Anything can be
overridden by an environment variable named `FITBIT_SYNC_<SECTION>_<KEY>`, for
example `FITBIT_SYNC_DATABASE_PATH=/mnt/backup/fitbit.sqlite3`.

The settings worth knowing:

| Setting | Default | Why you would change it |
| --- | --- | --- |
| `sync.window_days` | `3` | How many days the daily sync re-reads |
| `sync.data_source_family` | `all-sources` | `google-wearables` excludes manual logs |
| `sync.prefer_reconciled` | `true` | `false` reads the raw per source stream instead |
| `sync.max_requests_per_minute` | `60` | Raise or lower to match your live quota |
| `sync.skip_data_types` | `[]` | Skip data types you do not care about |
| `auth.extra_scopes` | `[]` | `["ecg"]` and `["irn"]` add those data types |
| `server.expose_lan` | `false` | `true` serves the dashboard on the LAN |

---

## What is stored

One SQLite file with two layers.

`raw_payloads` keeps every API response verbatim, keyed by data type, range, and
a content hash. Re-running a sync over unchanged data adds nothing; a genuinely
changed upstream response is stored as a new version. An upstream schema change
can therefore never lose data, and normalized tables can be rebuilt from the
archive.

Normalized tables are what reports query:

| Table | Contents |
| --- | --- |
| `daily_summary` | One row per day, derived, rebuilt after every sync |
| `activity_intervals` | Steps, distance, floors, AZM, calories, active minutes, zones |
| `heartrate_intraday` | Every heart rate sample |
| `sleep_sessions`, `sleep_stages` | Sessions with summaries and the stage timeline |
| `hrv`, `hrv_daily` | HRV samples and daily summaries |
| `spo2`, `spo2_daily` | SpO2 samples and daily summaries |
| `breathing_rate_daily`, `breathing_rate_sleep` | Breathing rate overall and per stage |
| `skin_temp` | Nightly skin temperature and its 30 day baseline |
| `resting_hr`, `hr_zones_daily`, `vo2max_daily` | Daily heart rate figures |
| `workouts` | Exercise sessions |
| `weight`, `body_fat`, `height` | Body measurements |
| `sync_state`, `sync_runs`, `sync_run_items` | Coverage, checkpoints, run history |

Schema changes ship as numbered files in `fitbit_pipeline/migrations/` and are
applied automatically, inside a transaction, on the next run.

### Metrics the API does not expose

- **Stress and EDA.** No such data type exists in Health API v4. The Charge 6 EDA
  sensor's output is not reachable through this API.
- **The Fitbit sleep score.** Not exposed. The sleep report shows efficiency,
  which is minutes asleep divided by minutes in bed, and labels it as such rather
  than approximating a 0 to 100 number that would quietly disagree with the app.

---

## Troubleshooting

**`invalid_grant: Token has been expired or revoked` after about a week.**
The OAuth consent screen is still in Testing publishing status. Publish the app
to production (setup step 5), then run `fitbit-sync auth --force`.
`fitbit-sync doctor` prints the refresh token's age, which makes this obvious.

**`Error 403: access_denied` at the consent screen.**
The account is not on the test user list and the app is still in Testing, or you
signed in with a different account. Publish the app, or add the address under
Test users.

**"Google hasn't verified this app".**
Expected for a personal app. Click **Advanced**, then **Go to (app name) (unsafe)**.
Health scopes are classified Restricted, so verification would mean a full
security assessment. That is for apps serving other people.

**`fitbit-sync auth` refuses with a Workspace account message.**
The Health API does not support Google Workspace accounts. Sign in with the
personal Google account your Fitbit account was migrated to.

**`doctor` says no `legacyUserId`.**
The Fitbit account may not be migrated to Google sign in. Open the Fitbit app and
complete the migration, then re-run `fitbit-sync auth --force`.

**HTTP 403 on some data types but not others.**
A scope was not granted. `fitbit-sync doctor` lists missing scopes. Run
`fitbit-sync auth --force` to re-consent. `electrocardiogram` and
`irregular-rhythm-notification` need `auth.extra_scopes = ["ecg", "irn"]`.

**HTTP 429, or a backfill that crawls.**
You are hitting a quota. The client already backs off exponentially and honors
`Retry-After`; lower `sync.max_requests_per_minute` and re-run. The backfill
resumes from its checkpoint, so nothing is lost.

**A backfill was killed and I do not know where it stopped.**
`fitbit-sync status`. It shows the cursor per data type. Just run
`fitbit-sync backfill` again.

**The dashboard shows "No data stored" for today.**
Data arrives when the watch syncs to the phone and the phone syncs to Google. Run
`fitbit-sync daily` after the watch has synced. Yesterday is usually complete;
today usually is not.

**Numbers disagree with the Fitbit app.**
Check `sync.data_source_family`. `all-sources` includes manual logs and connected
apps; the Fitbit app may be showing a narrower set. `google-wearables` restricts
to tracker devices. The raw response for any day is in `raw_payloads` if you want
to see exactly what the API said.

---

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

The suite runs the whole ingestion path against recorded API fixtures through a
mock HTTP transport, so it needs no token and no network. It covers filter
construction per time model, pagination, reconcile fallback, retry and backoff,
normalization for every data type, idempotency, backfill resume after a simulated
kill, daily summary aggregation, and every dashboard view and CSV export.

Project layout:

```
fitbit_pipeline/
  cli.py           command line entry point
  config.py        TOML plus environment configuration
  auth.py          OAuth, token storage, precondition checks
  api.py           HTTP client: reconcile, pagination, throttling, retries
  datatypes.py     the data type registry, read from the v4 discovery document
  normalize.py     API payloads to normalized rows
  aggregate.py     the derived daily_summary table
  sync.py          backfill, incremental sync, checkpointing
  db.py            SQLite access and the migration runner
  migrations/      numbered .sql files
  reports/
    queries.py     every number the dashboard shows
    charts.py      inline SVG charts
    app.py         FastAPI routes and CSV export
    templates/     Jinja2 templates
docs/
  api-findings.md          metric to endpoint mapping, open questions answered
  health-v4-discovery.json the pinned discovery document this was built against
deploy/            systemd units and a cron example
tests/             pytest suite with recorded fixtures
```

### Why this stack

**FastAPI with server rendered templates and inline SVG, not a JavaScript charting
library.** The requirement is a local dashboard with no cloud dependency. A CDN
script tag would break that on a machine without internet; vendoring a bundle
would add a build step and hundreds of kilobytes of code nobody reviews. Charts
built as SVG strings in Python are pure functions, so they are unit testable, they
render identically offline, and the entire dependency list stays at six packages.

**SQLite with hand written SQL and a small migration runner, not an ORM.** One
user, one file, a schema that maps closely to the API's own shapes. An ORM would
add indirection over queries that are already the clearest description of what
each report means.

**google-auth and google-auth-oauthlib for OAuth, httpx for the API.** The OAuth
loopback flow and silent refresh are exactly what those libraries do well, and
getting token refresh subtly wrong is how unattended pipelines fail at 3am. The
data calls are plain REST, so they use httpx directly rather than a generated
client.
