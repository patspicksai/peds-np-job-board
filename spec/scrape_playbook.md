# Scrape playbook

The procedure a scraping agent follows for one region. The daily scheduled task fans out one agent per region with this playbook. Tools available: WebSearch, WebFetch, Read, Write. The shell cannot reach the web; do not try curl / requests.

Inputs: `data/hospitals.json` (or the subset for your region), `data/jobs.json` (existing jobs, so you can skip re-fetching descriptions you already have), `spec/jobs_schema.md`.

Output: `data/jobs_runs/<run_id>_<region_key>.json` per the jobs schema, plus a short report.

## Per hospital

Work through every `active` hospital in your region. For each one, do all three passes; different boards respond to different approaches.

### Pass A: the employer's own search page
1. WebFetch `careers_search_url` (or `careers_url` if null) with the prompt: "List every job posting on this page whose title includes nurse practitioner, NP, APP, advanced practice, APRN, CRNP, or physician assistant/NP. For each return: title, location, posted date if shown, job id if shown, and the full absolute URL of the posting. If the page shows a search box but no listings (JS-rendered), say NO_LISTINGS."
2. If NO_LISTINGS or the fetch fails, note it in `unreachable` only if BOTH pass B and C also find nothing; otherwise continue.
3. Keep only postings that pass the relevance filter in jobs_schema.md (pediatric terms AND advanced practice role).

### Pass B: targeted web search (catches JS-rendered boards such as Workday)
Run these searches (2 to 4 per hospital, adjust names to the system's naming):
- `<health system> pediatric nurse practitioner jobs`
- `<hospital short name> acute care pediatric nurse practitioner`
- `<health system> PICU nurse practitioner` and `<health system> NICU nurse practitioner`
- for children's hospitals also `<hospital> advanced practice provider pediatrics`
From the results keep links on the employer's own domain or ATS domain (myworkdayjobs.com, icims.com, taleo.net, successfactors.com, phenom, jobs.<system>.org, careers.<system>.org). Collect aggregator hits (Indeed, LinkedIn, Glassdoor) only as leads to find the employer posting; if you cannot locate the employer page after one attempt, keep the aggregator URL with `source` set accordingly.

### Pass C: known listing pages
Some systems have a "nursing / advanced practice" category page. If `careers_notes` names one, fetch it too.

## Per posting
For every candidate URL not already in jobs.json with `status: active` (compare normalized URL):
1. WebFetch the posting URL with the prompt: "Extract from this job posting: exact title, requisition/job id, location, department or unit, employment type, shift, schedule, salary if shown, posted date if shown, the complete job description text (responsibilities, qualifications, requirements, benefits) verbatim, and the apply URL. If the page says the job is no longer available or returns an error, say JOB_CLOSED."
2. Build the job object per jobs_schema.md. Write `summary` in your own words. Put the verbatim description in `description` (max ~6000 chars).
3. Score `relevance` honestly. Many general "Nurse Practitioner" postings at a children's hospital are relevance 2; an explicitly PICU/NICU/peds inpatient role is 3.
4. For URLs already in jobs.json, do not re-fetch; just list their slugs in a `seen_again` array so the merge can bump `last_seen_at`.

## Rate and scope
- Expect 2 to 6 tool calls per hospital, more for large systems. Do not skip hospitals. If a system (e.g. Atrium, Novant, Hackensack Meridian) posts all jobs on one board, fetch that board once and attribute postings to the hospital named in each posting's location.
- Do not invent postings, dates, salaries or IDs. Null is correct when the posting does not say.
- Record every hospital you checked in `checked` and every hospital whose board could not be read by any pass in `unreachable`.

## Report
Return (do not print the JSON): number of hospitals checked, number unreachable (with slugs and why), number of jobs found by relevance level, and the three most interesting postings with URLs. Note any board that needs a better `careers_search_url` and propose the fixed URL so the ledger can be updated.
