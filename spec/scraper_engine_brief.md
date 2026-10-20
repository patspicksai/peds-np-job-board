# Scraper engine (option 1b): fully automated, no Claude in the loop

Goal: a Python scraper that runs on GitHub Actions every morning, walks `data/hospitals.json`, pulls every current pediatric / acute care NP posting from each hospital's applicant tracking system (ATS) using direct APIs where they exist and headless Playwright otherwise, merges into `data/jobs.json`, commits, and sends a phone push via ntfy.sh. It must be robust to one board failing (log and continue) and must never wipe existing data.

Repo root = /home/claude/npjobs (becomes github.com/patspicksai/peds-np-job-board). Python 3.12.

## Layout

```
scraper/
  __init__.py
  run.py                  CLI: python -m scraper.run [--only slug,slug] [--limit N] [--no-details] [--dry-run] [--platform workday]
  config.py               keyword lists, relevance rules, paths, timeouts
  models.py               Job dataclass + to_dict, normalize_url(), job_id()
  relevance.py            is_candidate(title, text) -> (bool, relevance 0..3, setting, reason, tags)
  http.py                 requests.Session with retries, browser-like UA, per-host throttle, timeouts
  detect.py               detect_platform(hospital) from careers_platform + careers_search_url + careers_url host patterns
  merge.py                merge run results into data/jobs.json (first_seen/last_seen/stale/closed logic from spec/jobs_schema.md)
  notify.py               ntfy.sh push + optional email (SMTP env) summarizing new jobs
  adapters/
    __init__.py           registry: platform -> adapter class
    base.py               Adapter interface: search(hospital, keywords) -> list[Listing]; details(listing) -> Job
    workday.py            POST https://{host}/wday/cxs/{tenant}/{site}/jobs {"appliedFacets":{},"limit":20,"offset":N,"searchText":kw}; detail GET /wday/cxs/{tenant}/{site}{externalPath} -> jobPostingInfo (jobDescription HTML, jobReqId, startDate, location, timeType). Paginate until total. Derive tenant/site from ats_config or from careers_search_url (https://{tenant}.wd{n}.myworkdayjobs.com/{locale?}/{site}).
    icims.py              GET https://{host}/jobs/search?ss=1&searchKeyword={kw}&in_iframe=1 (HTML, server rendered); parse listing links /jobs/{id}/{slug}/job; detail page has JSON-LD JobPosting and description div.
    oracle_cloud.py       GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations,flexFieldsFacet.values,requisitionList.requisitionFlexFields&finder=findReqs;siteNumber={site},keyword={kw},limit=25,offset=0,sortBy=POSTING_DATES_DESC ; detail finder=findReqs;siteNumber=...,Id=... (recruitingCEJobRequisitionDetails) -> ExternalDescriptionStr etc.
    successfactors.py     GET https://{host}/search/?q={kw}&sortColumn=referencedate&sortOrder=desc&startrow={n} (HTML; rows a.jobTitle-link); detail page .jobdescription / JSON-LD.
    phenom.py             POST https://{host}/widgets with the refineSearch payload (ddoKey "refineSearch", keywords, from, size up to 50, jobs true); jobs list in refineSearch.data.jobs; detail via GET /en/job/{jobId}/... or widgets ddoKey "jobDetail"? Investigate on a live board and implement what works; also parse JSON-LD on the job page.
    ultipro.py            POST https://recruiting.ultipro.com/{board_path}/JobBoardView/LoadSearchResults with the standard payload; detail OpportunityDetail.
    eightfold.py          GET https://{host}/api/apply/v2/jobs?domain={domain}&query={kw}&start=0&num=10 (JSON); detail /api/apply/v2/jobs/{id}?domain=...
    taleo.py              Oracle Taleo legacy: try the REST job board search (careersection/rest/jobboard/searchjobs?lang=en&portal=...) and otherwise fall back to Playwright.
    pageup.py, cornerstone.py, adp.py, avature.py, dayforce.py: implement if a ledger hospital uses them; otherwise leave a clear NotImplemented that routes to the generic adapter.
    generic.py            requests first: fetch careers_search_url, extract links whose text or href looks like a job (regex on 'nurse practitioner', 'np', 'advanced practice', 'aprn'), fetch each, extract JSON-LD JobPosting or main text. Then Playwright fallback: load page, wait for networkidle, same extraction on rendered DOM, plus clicking "load more"/"next" up to 10 times. Also try the common pattern of appending ?q= / ?keywords= / ?search= to the base careers URL.
    aggregators.py        OPTIONAL extra coverage, each behind an env var so it is off by default: Adzuna (ADZUNA_APP_ID/KEY), Jooble (JOOBLE_KEY), SerpAPI Google Jobs (SERPAPI_KEY), Tavily (TAVILY_KEY). Query "pediatric nurse practitioner" per state, match employer name to ledger hospitals (fuzzy), use only to discover employer URLs, never as the canonical url unless nothing else.
tests/
  test_relevance.py, test_models.py, test_merge.py, test_detect.py, plus adapter parsers tested against small inline HTML/JSON fixtures you write by hand from the documented response shapes.
.github/workflows/scrape.yml   cron "15 10 * * *" (06:15 ET during DST; add a second cron "15 11 * * *" guarded by a timezone check, or just run both and let merge dedupe), workflow_dispatch with inputs only/limit/platform. Steps: checkout, setup-python 3.12, pip install -r scraper/requirements.txt, playwright install --with-deps chromium, python -m scraper.run, commit data/jobs.json + data/runs/*.json if changed (git config user, git add, git commit, git push), upload run log artifact, notify (python -m scraper.notify) using NTFY_TOPIC secret/var.
.github/workflows/pages.yml    deploy site/ (with data/ copied in) to GitHub Pages on push to main (actions/upload-pages-artifact + deploy-pages). Pages is only usable if the repo is public; the workflow must not fail the scrape workflow if Pages is disabled.
scraper/requirements.txt       requests, beautifulsoup4, lxml, playwright, python-dateutil, tenacity (keep it short)
```

## Behaviour

- Keywords searched per board: "nurse practitioner", "pediatric nurse practitioner", "advanced practice provider", "APRN", "PICU", "NICU". Dedupe by normalized URL within a run.
- Relevance filter and scoring exactly as spec/jobs_schema.md (shared with the Claude-driven path so both produce identical records).
- For every candidate listing not already in jobs.json (by normalized url), fetch details and build the full Job (description verbatim, cleaned from HTML to plain text with paragraphs preserved, max ~6000 chars).
- For listings already known, bump last_seen_at only.
- Hospitals whose adapter raised or returned zero listings on every keyword AND the platform is not "unknown": record in run `unreachable` with the exception text. A board that is reachable but has zero NP jobs is "checked" with 0 results, not unreachable.
- Output `data/runs/<YYYY-MM-DD>.json` with per-hospital status (platform used, listings seen, candidates, new, errors, seconds) so Pat can see which boards work.
- Concurrency: ThreadPoolExecutor with 6 workers across hospitals, per-host throttle of ~1 request/sec, total run budget 40 minutes (skip remaining and log if exceeded).
- Never commit if jobs.json fails to parse or if the new jobs.json has fewer than 50% of the previous active jobs (safety against a bad run wiping data); write to data/runs/ and exit non-zero instead.

## Testing while the GitHub repo may not exist yet

The workspace shell cannot reach the public internet (only GitHub and package registries). So: write unit tests against hand-written fixtures and run them locally with pytest. Build a `scripts/live_test.yml` GitHub workflow (workflow_dispatch) that runs `python -m scraper.run --limit 25 --no-commit` and prints the per-hospital status table, so the orchestrator can push and trigger it the moment the repo exists and read the logs with `gh run view --log`. Keep adapter code defensive: every parse wrapped so one weird response produces a logged failure, not a crash.

## Notifications

ntfy.sh: `POST https://ntfy.sh/{NTFY_TOPIC}` with title "Peds NP jobs: N new" and body listing hospital, title, link (ntfy supports an Actions header with a "view" button; add one pointing to the site URL from env SITE_URL). Topic comes from secret NTFY_TOPIC. Pat installs the ntfy app and subscribes to the topic. Also write the digest to data/runs/<date>.md for the Claude morning task to reuse.
