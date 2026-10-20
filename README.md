# NP Job Finder: custom acute care pediatric NP job board

A private, custom job board for Kaitlyn (acute care pediatric nurse practitioner) covering every hospital on the East Coast from South Carolina through Maine that could plausibly employ an acute care pediatric NP. Think "Indeed, but only the hospitals we care about, with the exact employer posting link, the full description, and a map."

Owner: Pat. Built October 2026.

## What it does

1. **Hospital ledger (gold source).** `data/hospitals.json` lists every candidate hospital with location, pediatric capabilities, careers site, photos and a written profile. Pat edits this file (or asks Claude to) to add or remove hospitals.
2. **Daily scrape.** Every morning a scheduled Claude task walks the ledger, finds every current pediatric / acute care NP posting at each hospital, and merges new ones into `data/jobs.json` with the exact employer URL and full description.
3. **Web page.** `site/index.html` shows a feed of the newest jobs synced to a map, a hospital ledger view, and a detail panel per hospital. Published as a Claude artifact (shareable link) and portable to any static host.
4. **Notifications.** The scheduled task's completion summary is pushed to Pat's phone / email each morning when new jobs are found. Browser push comes later once the page is on a real domain.

## Why plain JSON files and no database

- Data is small (hundreds of hospitals, low thousands of jobs) and written once a day in a batch.
- Files are owned outright, diffable, and portable to GitHub Pages / Netlify with zero changes.
- No backend, no auth, no keys in the page. Anyone with the link can view.
- The workspace that runs the scrape cannot reach the open web from its shell, so the scrape is Claude-tool driven (web search + fetch), and files are the natural output of that.

## Repository layout

```
npjobs/
  README.md                  this file
  spec/
    hospital_schema.md       field-by-field contract for hospitals.json
    jobs_schema.md           field-by-field contract for jobs.json
    regions.md               controlled region list + hotspot flags
    scrape_playbook.md       the exact procedure the daily scrape follows
  data/
    hospitals.json           GOLD ledger (merged from data/regions/*.json)
    jobs.json                accumulating job store
    regions/                 per-region research output (inputs to the merge)
    jobs_runs/               per-run scrape output before merge
  site/
    index.html               the web page (self-contained)
    data/hospitals.json      copy published with the page
    data/jobs.json           copy published with the page
  scripts/
    merge_hospitals.py       regions/*.json -> hospitals.json (validates schema)
    merge_jobs.py            jobs_runs/*.json + existing jobs.json -> jobs.json
    validate.py              schema + sanity checks (coords in bounds, URLs, dupes)
```

## Data flow

```
research agents ──> data/regions/*.json ──merge──> data/hospitals.json ──┐
                                                                        ├──> site/data/*.json ──> published artifact
daily scrape agents ──> data/jobs_runs/<date>.json ──merge──> data/jobs.json ──┘
```

Gold sources:
- Hospitals: the `hospitals.json` doc in the NP Job Finder claude.ai project (mirrored to data/hospitals.json and the artifact).
- Jobs: the `data/jobs.json` file published with the artifact (read back each morning before merging).

## Daily scrape (scheduled task)

Runs at ~6:10am ET every day as a fresh Claude session. Procedure is in `spec/scrape_playbook.md`. In short:
1. Read the hospital ledger from the project doc. Read current jobs.json from the artifact.
2. Fan out one subagent per region. Each subagent: for each hospital, search for pediatric / acute care NP postings, fetch the employer careers page, fetch each candidate posting, extract fields, score relevance, return JSON.
3. Merge: new URL -> insert with first_seen; known URL -> bump last_seen; known URL not seen for 7+ days while the hospital's source was reachable -> status "stale".
4. Republish artifact with updated jobs.json. Write a short run log. The final summary (new jobs by hospital with links) is what gets pushed to Pat.

## Adding or removing a hospital

Edit `hospitals.json` in the project (or tell Claude). Required fields are in `spec/hospital_schema.md`. Set `"active": false` to hide a hospital without deleting its history.

## Future upgrades (not built tonight)

- Host on GitHub Pages / Netlify for a real domain and browser push notifications (service worker).
- Email digest to Kaitlyn directly.
- Code adapters for Workday / iCIMS JSON APIs run from Pat's Mac via the desktop link, for faster and more complete coverage of JS-rendered boards.
- Commute isochrones from candidate rental areas to each hospital.
