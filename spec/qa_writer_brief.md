# QA writer brief (no-web polishing pass on enriched region files)

Purpose: the seed pass wrote records with a strict web budget, so many descriptions are thin and many facts are flagged low confidence. The enrichment job has since attached `_enrich.wikipedia` (article title, url, intro extract) and filled coordinates, photos and careers notes. This pass makes each assigned record read well and be internally consistent, WITHOUT web calls unless your prompt grants a small budget.

Read first: /home/claude/npjobs/spec/hospital_schema.md and the region file(s) named in your prompt.

For each assigned record:
1. Rewrite `description` (4 to 6 sentences, your own words, no copied sentences) using: the Wikipedia extract in `_enrich.wikipedia.extract`, the existing description, `notes`, `highlights`, and the pediatric fields. Cover: what the hospital is and who runs it, size and role in its region, what the pediatric program includes (be precise about what is confirmed vs reported), and one or two sentences about the town and surroundings as they relate to living there (coast, commute, nearby cities). Never invent specifics. If the extract and the record disagree, prefer the extract for institutional facts (ownership, beds, trauma level) and say "reportedly" for pediatric details you cannot confirm.
2. Update facts the extract confirms: `beds`, `health_system`, `affiliated_university`, `teaching`, `peds_trauma_level`, `nicu_level` (only if stated), `parent_hospital`. Leave null if the extract does not say.
3. Rewrite `highlights` as 3 to 6 crisp bullets that are true per the record and extract.
4. Fill `careers_search_url` when `careers_platform` is known and the pattern is standard: workday `https://<host>/<site>?q=nurse%20practitioner` (host and site from ats_config or careers_url), icims `https://<host>/jobs/search?ss=1&searchKeyword=nurse+practitioner&in_iframe=1`, successfactors `https://<host>/search/?q=nurse+practitioner`, phenom `https://<host>/en/search-results?keywords=nurse%20practitioner`, oracle_cloud leave as is, jibe/talentbrew `<careers_url>?k=nurse+practitioner` only if careers_url is a search page. Leave null when unsure.
5. Set `confidence`: "medium" if the extract corroborates the hospital's existence, size and that it has pediatric services (or the record came from a research pass with sources); "low" if pediatric inclusion still rests on a single unverified fact; "high" only if the record has real sources read in an earlier pass and nothing conflicts.
6. Keep every other field as is. Keep `_enrich`. Keep slugs. No em dashes anywhere.

Save with the append helper after every 5 records: `python3 -I /home/claude/npjobs/scripts/append_hospital.py <file> "<region_group>" <batch.json>` (it replaces by slug). Validate with `python3 -I /home/claude/npjobs/scripts/validate.py <file>` (full mode; coordinates must exist). Do not run git.

Report (under 300 words): records polished, facts changed (slug: field old -> new), records you believe should be deactivated or re-typed and why, remaining uncertainties.
