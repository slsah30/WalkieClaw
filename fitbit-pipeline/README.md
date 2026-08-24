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

### 5. Leave the app in Testing, and add yourself as a test user

**Do not publish the app to production.** Every `googlehealth.*` scope is
classified **Restricted**. Google blocks consent for restricted scopes on an
unverified app that is In production, with:

> Access blocked: (app name) has not completed the Google verification process

There is no **Advanced** / **unsafe** escape from that screen. Restricted scopes
in production require full verification plus a third party CASA security
assessment, which is priced for companies serving other people, not for one
person reading their own data.

Testing is the configuration that works:

1. Still on **OAuth consent screen**, confirm **Publishing status** is **Testing**.
   If you already published, use **Back to testing**.
2. Under **Test users** (called **Audience** in the newer console layout), click
   **Add users** and add your own Google address, the same personal account your
   Fitbit data lives on.

Test users can consent to restricted scopes on an unverified app. The visible
consequence is the "Google hasn't verified this app" warning at the single
consent screen in step 7, which you click through via **Advanced**. That is
expected.

**The cost of Testing: refresh tokens expire after 7 days.** Google expires every
refresh token issued by an app in Testing status, so the unattended daily sync
stops roughly weekly and you re-run `fitbit-sync auth --force`. `fitbit-sync
doctor` prints the token's age so you can see it coming, and a failed sync says
exactly this rather than printing a stack trace.

This is a genuine limitation of the Health API for personal use, not a
misconfiguration. Google's Health API documentation describes a **personal use
exception** to restricted scope verification; if you want to pursue a
longer lived setup, <https://developers.google.com/health/app-verification> is
the authoritative page, and it is worth re-reading before accepting the weekly
re-auth as permanent.

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

The dashboard binds to `127.0.0.1` and has no authentication. The bind address
is therefore the only access control it has, so widen it deliberately.

### Reaching the dashboard from your other devices, over Tailscale

This is the recommended way to read your data from a phone or a laptop
elsewhere. Set under `[server]`:

```toml
tailscale = true
```

`fitbit-sync serve` then binds this machine's tailnet address, so the dashboard
is reachable from devices on your tailnet and from nothing else. It is not on
your local network, not on any other interface, and not on the internet. If no
tailnet address is found, startup fails and says why rather than falling back to
a wider address.

```
$ fitbit-sync serve
The dashboard has no authentication. Binding 100.101.102.103 makes your health
data readable by anything that can reach it (tailnet only).
Dashboard on http://100.101.102.103:8722  (tailnet only)
```

Open that address from any device on the tailnet. With MagicDNS on you can use
the machine name instead, for example `http://my-server:8722`.

**The HTTPS alternative.** Leave the config alone, keep the dashboard on
`127.0.0.1`, and let Tailscale proxy it:

```bash
fitbit-sync serve                  # still 127.0.0.1 only
tailscale serve --bg 8722          # proxy it onto the tailnet over HTTPS
tailscale serve status
tailscale serve --https=443 off    # stop
```

That gives a real certificate and a MagicDNS hostname, and the listening socket
never leaves loopback. Prefer it if you want HTTPS; prefer `tailscale = true` if
you want one config line and no second process.

**Do not run `tailscale funnel`.** Funnel publishes the service to the public
internet. This dashboard has no authentication and shows your health data, so
funnel would make all of it world readable to anyone with the URL.

Everyone on your tailnet can reach the dashboard, including devices you have
shared with other people. Tailscale ACLs are the way to narrow that if your
tailnet is not only yours.

### Reaching it over the LAN instead

`expose_lan = true` binds `0.0.0.0`, which serves the dashboard to everything on
the local network. Tailscale is the better answer in almost every case. Do not
port forward either one.

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
| `server.tailscale` | `false` | `true` binds this machine's tailnet address, reachable from your tailnet only |
| `server.expose_lan` | `false` | `true` serves the dashboard on the LAN. `tailscale` wins if both are set |

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

**`Access blocked: (app) has not completed the Google verification process`.**
The app is published **In production** with restricted Health scopes, which
Google blocks for unverified apps. Set the publishing status back to **Testing**
and add your address under **Test users** (setup step 5), then retry. There is no
click through option on this screen; the status has to change.

**`invalid_grant: Token has been expired or revoked` after about a week.**
Expected on this setup. Apps in Testing status get refresh tokens that expire
after 7 days, and Testing is the only status that permits restricted scopes
without verification. Run `fitbit-sync auth --force` to re-consent.
`fitbit-sync doctor` prints the refresh token's age so you can re-auth before the
next sync fails rather than after.

**`Error 403: access_denied` at the consent screen.**
The account is not on the test user list, or you signed in with a different
account than the one you added. Add the address under **Test users**, and check
which account the browser was signed in as.

**"Google hasn't verified this app".**
Expected for a personal app, and safe to click through: **Advanced**, then
**Go to (app name) (unsafe)**. This is the warning interstitial, not the hard
block described above. Health scopes are Restricted, so real verification would
mean a full CASA security assessment, which is for apps serving other people.

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
