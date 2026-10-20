# Peds NP Job Board

A private, curated job board for acute care pediatric nurse practitioner roles at every hospital on the US East Coast from South Carolina through Maine that could plausibly employ one. Think "Indeed, but only the hospitals we care about, with the exact employer posting link, the full description, and a map."

Live site: https://patspicksai.github.io/peds-np-job-board/

## How it works

```
data/hospitals.json  (gold ledger, ~500 hospitals)
        |
        v
GitHub Actions: scrape.yml  (daily 06:15 ET, Python + Playwright, no AI)
        |  per-platform adapters: Workday, iCIMS, Oracle Cloud HCM, SuccessFactors, Phenom,
        |  Jibe, TalentBrew, Symphony, Taleo, UKG, Eightfold, Hopkins, generic + headless Chrome
        v
data/jobs.json  (append-only job store; merge never deletes)  --> commit --> gh-pages deploy --> site
        |
        v
Claude scheduled task (07:35 ET): reads the run, re-scrapes boards the engine could not read,
quality-checks new postings, refreshes the Claude artifact, sends the digest to phone + email
```

Two engines, on purpose:
- **1b, GitHub Actions (primary).** Fully automated, runs on GitHub's infrastructure with unrestricted internet. Reads most boards through their JSON APIs and the rest with headless Chrome. Results, status tables and a markdown digest land on the `ci-logs` branch under `logs/scrape/<run id>/`.
- **1a, Claude morning task (safety net).** Boards that block automation or have no recognizable listing page get a web-search-and-fetch pass; the task also validates new postings and writes the digest that is pushed to the owner's phone.

## Layout

```
README.md
spec/
  hospital_schema.md      field contract for hospitals.json (inclusion rules, geocoding, photos, ats_config)
  jobs_schema.md          field contract for jobs.json (relevance scoring, settings, statuses)
  regions.md              controlled region list and hotspot flags
  scrape_playbook.md      procedure the Claude-driven scrape follows
  scraper_engine_brief.md architecture of the automated engine
  daily_task_prompt.md    the morning scheduled task's prompt
data/
  hospitals.json          GOLD ledger (merged from data/regions/*.json by scripts/merge_hospitals.py)
  jobs.json               accumulating job store
  regions/*.json          per-region research files (edit these, then re-merge)
  runs/                   per-run scrape output (compacted after merge)
site/
  index.html              the board: one self-contained page (vanilla JS, Leaflet, OpenStreetMap tiles)
  smoke_test.mjs          jsdom test (npm install jsdom; node smoke_test.mjs)
scraper/                  the automated engine (python -m scraper.run --help)
scripts/
  validate.py             schema and sanity checks for hospital files (--seed allows pending coordinates)
  merge_hospitals.py      regions/*.json -> data/hospitals.json
  merge_jobs.py           run file -> data/jobs.json with first_seen / last_seen / stale / closed rules
  enrich_hospitals.py     Actions job: Wikipedia extract, coordinates, photos, careers platform detection
  apply_enrichment.py     applies an enrichment result onto a region file (fills only missing fields)
  append_hospital.py      atomic append/replace of records in a region file
.github/workflows/
  scrape.yml              daily scrape (cron 10:15 and 11:15 UTC; second skips if the first ran); workflow_dispatch inputs: only, limit, platform, ledger, notify, commit
  pages.yml               deploys site/ + data/ to the gh-pages branch after every scrape or site change
  enrich.yml              runs scripts/enrich_hospitals.py on Actions; modes: full, photos_only, regeocode
  live_test.yml           dry run of the scraper against any ledger; results to ci-logs
  screenshot.yml          Playwright screenshots of any URL (desktop + phone) to ci-logs
  netcheck.yml, probe.yml network probes used during development
```

## Operating it

- **Add or remove a hospital:** edit the record in the matching `data/regions/*.json` (set `"active": false` to hide one), run `python3 scripts/validate.py --seed data/regions/<file>.json`, then `python3 scripts/merge_hospitals.py`, commit, push. The site and the next scrape pick it up. Optionally run `enrich.yml` on the file to fill coordinates, photos and careers details.
- **Run a scrape now:** `gh workflow run scrape.yml --ref main` (add `-f commit=false` for a test run; `-f only=slug1,slug2` to target boards).
- **See what a run did:** `git fetch origin ci-logs` then `git show origin/ci-logs:logs/scrape/<run id>/status.txt` (per-hospital status), `digest.md`, `run_tail.log`.
- **Phone push without Claude:** install the ntfy app, pick a private topic name, subscribe to it, and set it as the repo variable `NTFY_TOPIC` (Settings > Secrets and variables > Actions > Variables, or `gh variable set NTFY_TOPIC --body <topic>`). The scrape then posts "N new jobs" with links after every run.
- **Local run:** `pip install -r scraper/requirements.txt && python -m playwright install chromium && python -m scraper.run --ledger data/hospitals.json --report-dir out --dry-run --limit 10`.

## Data notes

- Hospital facts come from state licensing lists, hospital association directories, hospital and health-system pages, Wikipedia extracts, and a knowledge-based seed pass where the web budget ran out; each record carries `confidence` (high / medium / low) and `sources`. Low-confidence records are included deliberately (the job scrape is the ground truth for who actually hires), but their pediatric flags should be read as "reported".
- Coordinates come from Wikipedia article coordinates, OpenStreetMap Nominatim, hospital directions pages, or a manual street-address lookup; `coord_source` says which.
- Photos are the hospital's Wikipedia lead image (with Commons credit and license) or, failing that, the hospital website's share image; each entry records its source page.
- Job records are the employer's own posting (URL, title, description verbatim, requisition id); aggregator URLs are only used when no employer page exists and are marked in `source`.
- `relevance`: 3 = explicitly pediatric and acute / inpatient / PICU / NICU / peds ED; 2 = pediatric NP with unclear setting or acute care NP at a children's hospital; 1 = NP role at a hotspot hospital that is not clearly pediatric (kept for context).

## Known gaps (as of 2026-10-20)

- On the second automated pass (2026-10-20, after the board repairs) 393 of 509 boards returned listings and 116 did not: Infor CloudSuite boards (Northern Light, Kaleida) have no adapter yet, a few sites still bot-block headless Chrome, and some community hospitals have no recognizable listing page. The morning Claude task covers these, and each ledger record's `careers_notes` says what was tried.
- Many smaller hospitals have no Wikipedia article, so some photos are website share images and some descriptions are shorter.
- `summary` on jobs is currently derived from the title; the morning task can enrich it.
- Browser push notifications from the page itself (service worker) are not wired yet; ntfy and the Claude digest cover notifications.
