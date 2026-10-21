# Peds NP Job Board

A private, curated job board for acute care pediatric nurse practitioner roles at every hospital on the US East Coast from South Carolina through Maine that could plausibly employ one. Think "Indeed, but only the hospitals we care about, with the exact employer posting link, the full description, and a map."

Live site: https://patspicksai.github.io/peds-np-job-board/

## How it works

```
data/hospitals.json  (gold ledger, ~500 hospitals)
        |
        v
GitHub Actions: scrape.yml  (daily, cron 02:15 UTC; Python + Playwright, no AI anywhere in the loop)
        |  job 1 "scrape": every board. Per-platform adapters: Workday, iCIMS, Oracle Cloud HCM,
        |    SuccessFactors, Phenom, Jibe, TalentBrew, Symphony, Taleo, UKG, Eightfold, Hopkins,
        |    CareerSite, generic + headless Chrome
        |  job 2 "retry": a fresh runner (new IP) re-scrapes only the boards job 1 lost to bot walls,
        |    rate limits, timeouts or connection errors, and folds the results into the same run
        v
data/jobs.json (append-only; merge never deletes) + data/coverage.json (per-board health)
        |
        v
commit --> gh-pages deploy --> site (Jobs, Hospitals, Coverage views)
```

One engine, no AI in the loop. Results, status tables and a markdown digest land on the `ci-logs` branch under `logs/scrape/<run id>/` (and `.../retry/` for the second pass). Claude is used only to build and to fix scraping gaps; the **Coverage** view on the site lists every board the last run could not read, why, and for how long, and "Copy gap list" puts that list on the clipboard to paste into a chat.

GitHub starts cron runs late (observed 4 to 5 hours after the cron minute), so the cron is set early (02:15 UTC, 22:15 ET) and the finished board lands overnight or by early morning Eastern either way. A second cron an hour later is skipped by the guard step when the day's scrape already committed.

## Layout

```
README.md
spec/
  hospital_schema.md      field contract for hospitals.json (inclusion rules, geocoding, photos, ats_config)
  jobs_schema.md          field contract for jobs.json (relevance scoring, settings, statuses)
  regions.md              controlled region list and hotspot flags
  scrape_playbook.md      procedure a Claude-driven gap-fill scrape follows (manual use only)
  scraper_engine_brief.md architecture of the automated engine
  daily_task_prompt.md    retired: prompt of the Claude morning task that was removed on 2026-10-20
data/
  hospitals.json          GOLD ledger (merged from data/regions/*.json by scripts/merge_hospitals.py)
  jobs.json               accumulating job store
  coverage.json           per-hospital scrape health from the latest run (what the Coverage view shows)
  regions/*.json          per-region research files (edit these, then re-merge)
  runs/                   per-run scrape output (compacted after merge; the retry pass is folded in)
site/
  index.html              the board: one self-contained page (vanilla JS, Leaflet, OpenStreetMap tiles)
  smoke_test.mjs          jsdom test (npm install jsdom; node smoke_test.mjs)
  data/                   small fixtures for the smoke test and local previews (hospitals, jobs, coverage)
scraper/                  the automated engine (python -m scraper.run --help)
scripts/
  validate.py             schema and sanity checks for hospital files (--seed allows pending coordinates)
  merge_hospitals.py      regions/*.json -> data/hospitals.json
  merge_jobs.py           run file -> data/jobs.json with first_seen / last_seen / stale / closed rules
  build_coverage.py       run file -> data/coverage.json (failure class, hint, streaks; --prev for the baseline)
  combine_runs.py         folds the retry pass into the day's run file; --select picks the boards worth retrying
  rescore_jobs.py         re-applies attribution and relevance caps to jobs.json after a rule change (--apply)
  enrich_hospitals.py     Actions job: Wikipedia extract, coordinates, photos, careers platform detection
  apply_enrichment.py     applies an enrichment result onto a region file (fills only missing fields)
  append_hospital.py      atomic append/replace of records in a region file
.github/workflows/
  scrape.yml              daily scrape + fresh-runner retry (cron 02:15 and 03:15 UTC; second skips if the first ran); workflow_dispatch inputs: only, limit, platform, ledger, notify, commit
  pages.yml               deploys site/ + data/ to the gh-pages branch after every scrape or site change
  enrich.yml              runs scripts/enrich_hospitals.py on Actions; modes: full, photos_only, regeocode
  live_test.yml           dry run of the scraper against any ledger; results to ci-logs; `impersonate` input (auto/off) A/B tests the curl_cffi bot-wall retry
  screenshot.yml          Playwright screenshots of any URL (desktop + phone) to ci-logs
  netcheck.yml, probe.yml network probes used during development
```

## Operating it

- **Add or remove a hospital:** edit the record in the matching `data/regions/*.json` (set `"active": false` to hide one), run `python3 scripts/validate.py --seed data/regions/<file>.json`, then `python3 scripts/merge_hospitals.py`, commit, push. The site and the next scrape pick it up. Optionally run `enrich.yml` on the file to fill coordinates, photos and careers details.
- **Run a scrape now:** `gh workflow run scrape.yml --ref main` (add `-f commit=false` for a test run; `-f only=slug1,slug2` to target boards).
- **See what a run did:** open the Coverage view on the site, or `git fetch origin ci-logs` then `git show origin/ci-logs:logs/scrape/<run id>/status.txt` (per-hospital status), `digest.md`, `run_tail.log`.
- **Fix a gap:** Coverage view > "Copy gap list", paste it into a chat with Claude (or work it by hand): dead URLs and marketing pages are ledger fixes (`careers_search_url` / `ats_config` in `data/regions/*.json`, then re-merge), missing vendors need an adapter in `scraper/adapters/`, bot walls are what the retry pass and `curl_cffi` are for. Test with `gh workflow run live_test.yml --ref main -f ledger=data/hospitals.json -f only=slug1,slug2`.
- **After changing relevance or attribution rules:** `python3 scripts/rescore_jobs.py` (dry run), then `--apply`, so postings already on the board get the same caps.
- **Phone push without Claude:** install the ntfy app, pick a private topic name, subscribe to it, and set it as the repo variable `NTFY_TOPIC` (Settings > Secrets and variables > Actions > Variables, or `gh variable set NTFY_TOPIC --body <topic>`). The scrape then posts "N new jobs" with links after every run.
- **Local run:** `pip install -r scraper/requirements.txt && python -m playwright install chromium && python -m scraper.run --ledger data/hospitals.json --report-dir out --dry-run --limit 10`.

## Data notes

- Hospital facts come from state licensing lists, hospital association directories, hospital and health-system pages, Wikipedia extracts, and a knowledge-based seed pass where the web budget ran out; each record carries `confidence` (high / medium / low) and `sources`. Low-confidence records are included deliberately (the job scrape is the ground truth for who actually hires), but their pediatric flags should be read as "reported".
- Coordinates come from Wikipedia article coordinates, OpenStreetMap Nominatim, hospital directions pages, or a manual street-address lookup; `coord_source` says which.
- Photos are the hospital's Wikipedia lead image (with Commons credit and license) or, failing that, the hospital website's share image; each entry records its source page.
- Job records are the employer's own posting (URL, title, description verbatim, requisition id); aggregator URLs are only used when no employer page exists and are marked in `source`.
- `relevance`: 3 = explicitly pediatric and acute / inpatient / PICU / NICU / peds ED; 2 = pediatric NP with unclear setting or acute care NP at a children's hospital; 1 = NP role at a hotspot hospital that is not clearly pediatric (kept for context). NICU roles that require an NNP credential and Physician Assistant titles that merely welcome NPs cap at 2 and are tagged ("NNP required", "PA title"). A children's hospital within a hospital counts as pediatric context only when the posting names it, not just the parent campus. All-ages boilerplate ("care for children and adults") is not pediatric evidence.
- System boards that carry postings for another member hospital in the same system and state attribute them to that record (the posting's location decides); a flagship fallback is noted in `relevance_reason`.

## Known gaps (as of 2026-10-21)

- The first scheduled run (2026-10-21) read 368 of 509 boards; the best pass so far (2026-10-20) read 393. Of the 141 not read: 53 reachable careers pages with no recognizable listings (marketing pages; ledger fixes), 51 bot walls (Cloudflare 403s on RWJBarnabas, UMMS, HCA, Penn Medicine, GW, UVM and a few others; these read fine in isolation and the retry pass exists to recover them), 32 dead or unreachable URLs (ledger fixes), 4 Infor CloudSuite boards without an adapter (Northern Light, Kaleida), 1 zero-result search. The Coverage view carries the live list and each ledger record's `careers_notes` says what was tried.
- Many smaller hospitals have no Wikipedia article, so some photos are website share images and some descriptions are shorter.
- `summary` on jobs is derived from the title and description.
- Notifications: ntfy is wired (set `NTFY_TOPIC`); a Telegram channel for a second subscriber is the planned next step. Browser push from the page itself (service worker) is not wired.
