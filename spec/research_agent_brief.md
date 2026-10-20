# Research agent brief

You are building one region's slice of a hospital ledger for a custom job board. The end user is an acute care pediatric nurse practitioner looking for inpatient / PICU / NICU / peds ED / peds hospitalist roles anywhere on the East Coast from South Carolina through Maine. Your slice must be EXHAUSTIVE: every hospital in your assigned states that could plausibly employ an acute care pediatric NP, from the famous children's hospitals down to the 120-bed regional hospital with an 8-bed pediatric unit. Missing a hospital is the main failure mode. Spending 40 tool calls to be sure is fine.

Read first: `/home/claude/npjobs/spec/hospital_schema.md` (field contract and inclusion rules) and `/home/claude/npjobs/spec/regions.md` (controlled region names).

## Method

Phase 1, enumerate (cast the widest net, then filter):
1. WebSearch and WebFetch: Children's Hospital Association member list for your states; each state hospital association member directory; Wikipedia "List of hospitals in <state>"; the state Department of Health licensed hospital list; US News "Best Children's Hospitals" regional lists; each major health system's "locations / hospitals" page.
2. For every hospital you find, decide inclusion using the rules in hospital_schema.md. Keep a running list in a scratch file (`/home/claude/npjobs/data/regions/<your_file>.scratch.md`) so nothing is lost if you run long.
3. Second sweep: for every city over roughly 25,000 people in your states, search `<city> hospital pediatric unit` and `<city> hospital NICU`. Add anything new.
4. Third sweep: search `<state> pediatric intensive care unit`, `<state> level III NICU`, `<state> pediatric emergency department`, `<state> children's hospital`. Add anything new.

Phase 2, enrich each hospital (fetch the real pages; do not write from memory):
- Wikipedia article (facts, coordinates, lead image file name, history).
- Hospital's own About / pediatrics / NICU / PICU pages (services, beds, levels).
- The system's careers site: find the actual job search page and work out the platform from the URL (myworkdayjobs.com = workday, icims.com = icims, taleo.net = taleo, fa.*.oraclecloud.com = oracle cloud HCM, successfactors / "jobs.<x>.org/search/?q=" = successfactors, "/en/search-results" or "/widgets" = phenom, recruiting.ultipro.com = ultipro, eightfold.ai = eightfold, pageuppeople = pageup, csod.com = cornerstone). Build `careers_search_url` with "nurse practitioner" embedded as the query if the platform supports it, fetch it once with WebFetch to see whether listings render, and write what you saw in `careers_notes`. Fill `ats_config` from the URL.
- Coordinates: Wikipedia infobox first, then the hospital's directions page, then OpenStreetMap. Last resort: the places search tool if you have it. Confirm the point is on the hospital campus.
- Photos: the Wikipedia lead image via `https://commons.wikimedia.org/wiki/Special:FilePath/<File name>?width=1200` (spaces as %20). Add the hospital's own newsroom photo URL if you find a direct image link. 1 to 4 photos, with credit and license.
- Write `description` and `highlights` in your own words. Do not copy sentences from any source.

Phase 3, write the output file (`/home/claude/npjobs/data/regions/<your_file>.json`) in the exact schema, run `python3 -I /home/claude/npjobs/scripts/validate.py <your_file>` if that script exists, fix any errors, and then return your report.

## Report format (your final message)
- Hospital count by `hospital_type` and by `region`.
- Hospitals you considered and excluded, with the reason (one line each).
- Military / VA facilities noticed (not included).
- Records with `confidence: low` and what is uncertain.
- Careers platforms seen (counts) and any board where the search URL did not render listings.
Do not paste the JSON into the report; it is in the file.

## Incremental saving (mandatory)
The environment can restart without warning and background work is lost. So: write your output JSON file EARLY and keep it valid at all times. Create it with `{"region_group": "...", "hospitals": []}` before enrichment starts, then append each finished hospital record to the file as you complete it (read, append, write; or keep a Python helper that loads/appends/dumps). Never hold records only in memory. Helper: write each finished record (or a small batch) to a temp file and run `python3 -I /home/claude/npjobs/scripts/append_hospital.py /home/claude/npjobs/data/regions/<file>.json "<region_group>" /path/to/record.json`; it creates the file if needed, replaces records with the same slug, and writes atomically. Do not run git. Update the scratch file's status line with what you have finished so a restarted agent can resume. If your scratch file already contains an enumeration from an earlier attempt, reuse it instead of redoing phase 1.

## Rules
- Facts only from pages you read this session. Null beats a guess.
- One hospital per record. A children's hospital inside a parent hospital gets its own record (type `childrens_hospital_within_hospital`, with `parent_hospital` set). Do NOT also create a record for the parent unless the parent independently has a reason to be listed (it usually does not; the children's record covers it). Exception: list the parent when the children's "hospital" is really just a named pediatric floor and the posting would be under the parent's name; in that case make one record for the parent with `peds_inpatient: true` and mention the pediatric unit name in `notes`.
- No em dashes anywhere in the text you write.
- Keep going until the sweeps stop producing new hospitals. Expect 15 to 60 records depending on the region.
