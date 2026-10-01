# BTVAPT

Burlington VT apartment intelligence tracker: listings plus the landlords,
managers, contacts and buildings behind them, with history for predicting
turnover. Single-user, runs on an always-on box reachable over Tailscale.

## Stack

Python 3.11+, SQLite (WAL) + Alembic, FastAPI JSON API, a static status/map
page (Leaflet later), Typer/Rich CLI, `httpx` for sources, Playwright only as
a fallback (one browser context at a time).

## What it does

- **Explore** (`/`): every unit on a map (clustered, Clean/Detailed/Satellite
  basemaps) and in a list, coloured by how its move-in date compares to your
  target. Filter pills for price, beds, town, pets, private landlords, scams,
  your tags/status; "only what's on the map"; right-click to drop a pin (work,
  campus) and see walking times. The unit panel shows photos, the exact text
  that set the move-in date, a scam check, your rating/status/tags/notes, who
  owns and manages it, every source link, price and status history and a
  turnover estimate.
- **Inbox**: posts from people. A bookmarklet saves any Facebook group post,
  Marketplace listing, Front Porch Forum post or web page in one click;
  pasting works too; an optional IMAP reader picks up forwarded notification
  emails. Leads get date parsing, address mapping, contact matching and scam
  checks, plus New/Keep/Contacted/Dismiss triage.
- **People**: owners (LLCs and people behind buildings), individuals (private
  landlords from Craigslist and saved posts) and property managers, each with
  their places, listing seasonality, outreach log and notes.
- **Health**: per-source status with pause/run, live job progress, backups.

The target move-in date and near-miss window are view settings only; ingest
always keeps everything.

### Scam checks

Every listing gets a transparent score with reasons: payment by wire/gift
card/crypto, deposit to hold before a showing, "I'm out of the country",
"keys will be mailed", rent far below comparable units, and above all text
or photos copied from a property manager's real listing (the most common
local scam). Manager-site listings are marked as verified sources. Likely
scams are hidden by default (toggle under More).

### Facebook, Front Porch Forum and other people-posted rentals

Facebook can't be scraped reliably or within its terms, so the app meets you
where you browse: drag **Save to BTV** from the Inbox page to your bookmarks
bar, then click it on any post. For hands-off capture, create a dedicated
Gmail, point Facebook group notifications and Front Porch Forum digests at
it, and add to `btv.toml`:

```toml
[inbox]
host = "imap.gmail.com"
user = "you.rentals@gmail.com"
password = "gmail-app-password"   # or set BTV_INBOX_PASSWORD
folder = "INBOX"
```

Rental-looking emails become leads every 30 minutes.

## Layout

| Path | What |
|---|---|
| `btv/models.py` | Schema: sources, jobs, scrape runs, raw archive, listings + immutable snapshots, buildings/units + merge log, contacts, user notes/tags |
| `btv/jobs/` | Job runner (persistent progress, cancel, stale recovery, one-at-a-time lock) and the scheduler/worker loop |
| `btv/ingest.py` | `scrape` job: adapter run, raw archiving, snapshots on change, status lifecycle, then the pipeline |
| `btv/pipeline.py` | Per-snapshot: availability parsing, address linking to units/buildings (audited), contact extraction; `rederive` job |
| `btv/normalize/` | Address, availability-date, amenity and contact normalizers |
| `btv/geocode.py` | `geocode` job (Census, Nominatim fallback) for buildings without source coordinates |
| `btv/views.py` | Read models: unit summaries (per-field source priority), unit detail, contacts, companies |
| `btv/actions.py` | Notes/tags, merge/split/undo units, merge contacts, mention review, outreach |
| `btv/health.py` | Run health checks (expected counts, sudden drops) and per-source dashboard state |
| `btv/http.py` | Polite client: robots.txt, per-host rate limit, retries with backoff |
| `btv/sources/` | Adapters: `buildium`, `nesthub`, `appfolio`, `craigslist`, `manual`/`email` (passive), `file` (tests) |
| `btv/owners.py` | `owners` job: owner entities behind buildings from ShowMeTheRent |
| `btv/inbox.py` | Leads from the bookmarklet/paste and the IMAP `inbox` job |
| `btv/normalize/scam.py` | Scam risk scoring with reasons |
| `btv/static/` | The UI: `index.html`, `app.js`, `app.css`, vendored Leaflet |
| `btv/backup.py` | Online SQLite backup, gzip, 14 daily + 8 weekly retention |
| `config/sources.toml` | Source definitions (synced into the DB) |
| `tests/fixtures/` | Saved real responses per platform; parser tests run against them |

## Key behaviours

- **Ingest everything, filter at view time.**
- **Every listing has its source URL**; a listing without one fails the run.
- **Raw archive:** every response is stored gzipped and content-addressed in
  `data/archive`, linked to its scrape run, so parsers can be re-run
  (`btv run rederive`).
- **Move-in dates:** structured field and description-derived date are stored
  separately with raw text, confidence and method. A date in the description
  wins ("available now" field vs "available July 22nd" text), except that a
  month-only phrase doesn't override a precise date in the same month.
  Lease-end dates ("lease through 5/31") are ignored.
- **Dedupe:** addresses are normalized ("46 Lafountain St Apt 1" =
  "46 Lafountain Street #1") and listings from any source link to the same
  unit. Every link/merge/split is in `merge_log` and can be undone; manual
  splits lock the listing so auto-linking never overrides you. Field conflicts
  resolve by source priority (manager sites first), then recency, with all
  source links kept.
- **Gone detection:** a listing missing from `gone_after_misses` (default 3)
  consecutive *healthy* runs is marked gone; one that returns is `relisted`.
  Runs with implausible counts never count as misses.
- **Contacts:** phones/emails normalized; showing-service relay emails are
  attributed to the company; fuzzy name matches (e.g. "J. Smith" vs
  "Jane Smith") auto-link above 0.9 similarity and go to review above 0.75.
- **Kill switch:** per source in the Health tab, `btv sources disable`, or
  the API. Failing sources back off exponentially (capped at one day).

## Sources

| Source | Platform | Notes |
|---|---|---|
| Hinsdale | `buildium` | `ApartmentSearch.aspx` holds every listing; detail/images pages add amenities and photos |
| Five Seasons | `nesthub` | burlingtonproperty.management `/_system/api/listings` JSON (their Buildium public page is disabled) |
| Stone & Browning, Fusion, RPM Sterling, Distinctive, Bissonette, Strong Will, Full Circle, Farrell, S.D. Ireland, Larkin, Nedde, Appletree Bay, Gardner, Queen City, Colchester PM | `appfolio` | Server-rendered list + detail pages; `robots_override = true` by explicit decision. Most were found via ShowMeTheRent. |
| Craigslist | `craigslist` | The site's own search JSON (12 mi around 05401) + post pages; `/reply` (contact reveal) is never fetched. Wheeler PM lists only here. |
| Owners | `owners` job | ShowMeTheRent's public search names the owner entity for AppFolio-managed buildings |
| Saved by me / Email inbox | `manual` / `email` | Bookmarklet, paste and IMAP leads |
| Redstone | `rentcafe` (disabled) | Behind a Cloudflare browser challenge; needs a Playwright adapter |

Not covered: Zillow/Apartments.com (block automated access; their units are
mostly the managers above), Facebook (bookmarklet/email instead), UVM
off-campus housing and Seven Days classifieds (not reachable / no listings
feed found).

Detail pages are only refetched when a listing is new, its summary changed,
or `detail_refresh_hours` (default 24) elapsed, so routine runs are one or
two requests per source.

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
btv run geocode            # geocode buildings lacking coordinates
btv run rederive           # re-run date/address/contact parsing over stored snapshots
btv sources list|sync|enable|disable
btv settings get | btv settings set target_move_in 2027-06-01
```

### API (JSON)

- `GET /api/units` (filters: `match`, `min_rent`, `max_rent`, `min_beds`, `tag`, `city`, `q`, `include_gone`), `GET /api/units/{id}`
- `PUT /api/units/{id}/prefs`, `POST /api/units/{id}/tags`, `DELETE /api/units/{id}/tags/{tag}`
- `POST /api/units/merge`, `POST /api/listings/{id}/unlink`, `POST /api/merges/{id}/undo`
- `GET /api/contacts`, `GET /api/contacts/{id}`, `PUT /api/contacts/{id}`, `POST /api/contacts/{id}/outreach`, `POST /api/contacts/merge`, `POST /api/mentions/{id}/review`, `GET /api/companies`
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
