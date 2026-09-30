# BTVAPT

Burlington VT apartment intelligence tracker: listings plus the landlords,
managers, contacts and buildings behind them, with history for predicting
turnover. Single-user, runs on an always-on box reachable over Tailscale.

## Stack

Python 3.11+, SQLite (WAL) + Alembic, FastAPI JSON API, a static status/map
page (Leaflet later), Typer/Rich CLI, `httpx` for sources, Playwright only as
a fallback (one browser context at a time).

## Layout

| Path | What |
|---|---|
| `btv/models.py` | Schema: sources, jobs, scrape runs, raw archive, listings + immutable snapshots, buildings/units + merge log, contacts, user notes/tags |
| `btv/jobs/runner.py` | Job runner: persistent progress (percent, step, ETA), cancellation, stale-job recovery, one-job-at-a-time lock |
| `btv/jobs/worker.py` | Scheduler loop: enqueues due scrapes and a daily backup, drains the queue |
| `btv/ingest.py` | `scrape` job: runs an adapter, archives raw responses, snapshots on change, status lifecycle |
| `btv/health.py` | Run health checks (expected counts, sudden drops) and per-source dashboard state |
| `btv/http.py` | Polite client: robots.txt, per-host rate limit, retries with backoff |
| `btv/sources/` | Adapter interface + registry; `file` adapter for tests/manual paste-in |
| `btv/backup.py` | Online SQLite backup, gzip, 14 daily + 8 weekly retention |
| `config/sources.toml` | Source definitions (synced into the DB) |

## Key behaviours

- **Ingest everything, filter at view time.** `target_move_in` (default
  2027-06-01) and `near_miss_days` are runtime settings, never ingest filters.
- **Every listing has its source URL**; a listing without one fails the run.
- **Raw archive:** every response is stored gzipped and content-addressed in
  `data/archive`, linked to its scrape run, so parsers can be re-run later.
- **Snapshots** are immutable and written whenever a listing's content
  changes; unchanged sightings update `last_seen`/`last_verified`.
- **Gone detection:** a listing missing from `gone_after_misses` (default 3)
  consecutive *healthy* runs is marked gone. Runs with implausible counts
  (below `expected_min`, above `expected_max`, or under half the recent
  median) are unhealthy and never count as misses.
- **Kill switch:** `btv sources disable <id> --reason ...` (or the API).
  Failing sources back off exponentially (capped at one day).

## Running it

```bash
uv sync
cp btv.example.toml btv.toml     # set host to your Tailscale IP
uv run btv init                  # migrate DB + load config/sources.toml
uv run btv serve                 # API + status page + scheduler on :8321
```

Open `http://<tailscale-host>:8321/` for the status page.

### CLI

```bash
btv scrape <source>        # run now with a live progress bar (--queue to hand to the worker)
btv jobs --watch           # live progress of whatever the worker is doing
btv jobs --id 12           # one job as JSON, with its log
btv health [--json]        # per-source state; exit code 1 if anything is failing/stale
btv backup                 # manual backup
btv sources list|sync|enable|disable
btv settings get | btv settings set target_move_in 2027-06-01
```

### API (JSON)

- `GET /api/health`: overall + per-source state, last backup
- `GET /api/jobs`, `GET /api/jobs/{id}`: status, percent, step, ETA
- `POST /api/jobs` `{"kind": "scrape", "source_id": "..."}`, `POST /api/jobs/{id}/cancel`
- `GET /api/sources`, `GET /api/sources/{id}/runs`, `POST /api/sources/{id}/enabled`
- `GET|PUT /api/settings`
- `/docs`: interactive OpenAPI

### Deploy on the KVM

See `deploy/systemd/btv.service`. The worker runs inside `btv serve` and
performs a backup every 24h. To also keep the raw archive off-box, rsync
`data/archive` and `data/backups` elsewhere on the tailnet.

### Browser fallback (only when a source needs it)

```bash
uv sync --extra browser && uv run playwright install chromium
```

## Tests

```bash
uv run pytest
```
