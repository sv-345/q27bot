# q27bot — Summer 2027 Quant Intern Monitor

Polls three sources every 30 minutes for Summer 2027 quantitative trading /
research internships and pings a Discord webhook when something new lands.
Runs entirely on GitHub Actions; state lives as JSON in this repo, so the
git history doubles as a posting archive.

## Sources

| Source         | Surface                                                                                | Stable ID         |
|----------------|----------------------------------------------------------------------------------------|-------------------|
| SimplifyJobs   | `Summer2027-Internships/.github/scripts/listings.json` (falls back to `Summer2026-…`)  | `id` (UUID)       |
| Northwestern   | `northwesternfintech/2027QuantInternships/data/*.yaml`                                 | URL-as-ID         |
| Direct firms   | Auto-detected ATS embed (Greenhouse / Lever / Ashby) on each firm's careers page       | ATS posting `id`  |

Workday tenants are detected but not scraped — those firms land in
`unsupported.log`. Anything else (Jane Street, HRT, Citadel — the elite shops
mostly run their own systems) also lands there. That's expected; the two
community repos cover most of those firms.

## Setup

1. Fork or clone, push to a GitHub repo.
2. **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `DISCORD_WEBHOOK_URL`
   - Value: your Discord webhook URL (Server → channel → Edit Channel → Integrations → Webhooks).
3. Push. The workflow runs on push, on `*/30` cron, and on `workflow_dispatch`.
4. The first run is treated as a cold start: state is seeded and Discord gets
   a single "tracking N postings" message — not one ping per existing role.

## Local run

```bash
pip install -r requirements.txt

# See what would notify, hit no APIs Discord-side, don't write state:
python monitor.py --dry-run

# Seed an empty state file without notifying:
python monitor.py --seed

# Normal run (needs DISCORD_WEBHOOK_URL):
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... python monitor.py

# Tests:
python -m pytest tests/ -v
```

A second `python monitor.py` immediately after a successful run produces
zero new postings and writes an effectively-unchanged `state/postings.json`.

## Adding a firm

Append an entry to `firms.yaml` under `firms:`:

```yaml
- { name: "Foo Capital", careers_url: "https://foocapital.com/careers" }
```

On the next run, `detect.py` fetches the page, greps for an embedded ATS,
caches the result in `firms_detected.json`. If detection fails the firm
ends up in `unsupported.log` and is silently skipped on subsequent runs
(community sources still cover it).

To force re-detection after editing `careers_url`, delete the firm's entry
from `firms_detected.json`.

## Adding custom scrapers for elite shops

Most elite quant shops (Jane Street, Citadel, HRT, Two Sigma, IMC) don't
expose a public ATS API. To monitor them directly, drop a new file in
`sources/` modeled on `sources/greenhouse.py`:

```python
# sources/janestreet.py
from .base import Posting

def fetch(timeout: int = 30) -> list[dict]:
    # hit their listing endpoint or HTML-scrape
    ...

def parse(jobs: list[dict]) -> list[Posting]:
    return [Posting(firm="Jane Street", external_id=..., source="janestreet", ...) for j in jobs]
```

Then register it in `monitor.py` — add a call in `fetch_direct` (or a new
helper) that runs alongside the ATS clients. Use the firm's HTML element IDs
as `external_id` so notifications stay idempotent across runs.

Keep keyword filtering out of scrapers — `filters.py` runs after the merge.

## Profiles (multi-channel)

`firms.yaml` defines named **profiles** under `profiles:`. Each profile has
its own filter rules, Discord webhook (looked up by env-var name), and state
file. One fetch run, N filter passes — a posting matching multiple profiles
gets sent to all matching channels.

Default profiles:

| Profile | Webhook env var             | State file                    | Scope                                                       |
|---------|-----------------------------|-------------------------------|-------------------------------------------------------------|
| `quant` | `DISCORD_WEBHOOK_URL`       | `state/postings_quant.json`   | quant / trader / researcher / systematic / algo (intern only) |
| `tech`  | `DISCORD_WEBHOOK_URL_TECH`  | `state/postings_tech.json`    | SWE / data science / ML / AI / finance / S&T / banking — intern *or* "Summer Analyst" |

Per-profile filter knobs (under `profiles.<name>.filters:`):

- `must_include_any`: at least one must appear (word boundary). For `quant` this
  is `intern` / `internship`; for `tech` it also includes `summer analyst` and
  `summer associate` since banks brand intern roles that way.
- `must_include_role_any`: at least one role keyword must appear.
- `must_exclude_any`: substring blacklist (HR/marketing/legal/etc.).
- `pass_year` / `reject_years`: titles with `2027` always pass; `2024/25/26`
  without `2027` reject; no year at all → pass (recall over precision).

To run only one profile locally: `python monitor.py --profile tech --dry-run`.

## Adding a profile

```yaml
profiles:
  ml-research:                       # arbitrary name
    webhook_env: DISCORD_WEBHOOK_URL_ML
    state_file: state/postings_ml.json
    filters:
      must_include_any: [intern, internship]
      must_include_role_any: [machine learning, ml, ai, deep learning, research]
      must_exclude_any: [marketing, hr intern]
      pass_year: "2027"
      reject_years: ["2024", "2025", "2026"]
```

Then add the matching repo secret (`DISCORD_WEBHOOK_URL_ML`) and add it to
the `env:` block of the `Run monitor` step in
`.github/workflows/monitor.yml`. First run auto-seeds (single summary msg).

## State files

One JSON file per profile under `state/`, each keyed `"{firm}::{external_id}"`,
committed back each run by `stefanzweifel/git-auto-commit-action`. Disappeared
postings are kept with their `last_seen` timestamp updated — never deleted.

## Interpreting `unsupported.log`

One line per firm where ATS auto-detection didn't find a Greenhouse / Lever /
Ashby embed. Reasons:

- `unsupported`: the careers page renders client-side or uses a custom backend.
- `workday: needs implementation`: firm uses Workday — we detect it but
  don't scrape it (Workday boards return JSON only via `POST` with a CSRF
  token; lifting that is a separate effort).

These firms are still covered by SimplifyJobs and Northwestern, so dropping
into the log isn't a black hole — just a "no direct fast-path."

## File layout

```
q27bot/
├── monitor.py              entry point
├── filters.py              title-matching
├── detect.py               ATS auto-detection + cache
├── notify.py               Discord webhook (10/embed cap, 429-aware)
├── firms.yaml              firm list + filter rules + repo URLs
├── requirements.txt
├── sources/
│   ├── base.py             Posting dataclass
│   ├── simplify.py
│   ├── northwestern.py
│   ├── greenhouse.py
│   ├── lever.py
│   └── ashby.py
├── state/postings.json     committed each run
├── firms_detected.json     ATS detection cache
├── unsupported.log         firms where auto-detection failed
├── tests/
│   ├── test_filters.py
│   └── test_diff.py
└── .github/workflows/monitor.yml
```
