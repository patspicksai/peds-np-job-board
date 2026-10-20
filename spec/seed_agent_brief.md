# Seed agent brief (low web budget mode)

Why this mode exists: the deep research mode ran out of shared web-tool budget (the WebSearch pool is 200 calls per turn across all agents, and WebFetch rate-limits with HTTP 429). So this pass completes the ledger with a STRICT budget: at most 20 web tool calls (WebSearch + WebFetch combined) per agent, spent only on inclusion decisions you cannot make otherwise. Coordinates, Wikipedia extracts, lead photos, official websites and careers-platform detection are filled afterwards by an automated enrichment job that runs on GitHub Actions (scripts/enrich_hospitals.py), so DO NOT spend web calls on those.

Read first: /home/claude/npjobs/spec/hospital_schema.md (fields and inclusion rules), /home/claude/npjobs/spec/regions.md (region names), and the existing region file(s) and scratch file(s) named in your prompt. The scratch files hold enumerations from state hospital lists (names, cities, beds, systems, sometimes DOH bed-type lists and coordinates). Build on them.

## What to write

For every hospital in your scope that meets the inclusion rules and is NOT already in the region file (match by name; slugs must stay unique), write a full record with every schema key present:
- Fill from the scratch enumeration and from what you reliably know about the hospital (name, parent, system, type, city, state, address if known, website if known, system careers URL if known, affiliated university, beds, pediatric capabilities, services, nearest airport, notes).
- `lat`, `lng`: use coordinates from the scratch file if present (R3b's scratch has them for Upstate NY). Otherwise set both to null and `coord_source` to "pending enrichment". The enrichment job fills them.
- `wikipedia_title`: your best guess at the exact article title, or null.
- `photos`: [] (enrichment adds the Wikipedia lead image).
- `careers_platform`: what you know, else "unknown". `careers_search_url`: null unless you know the real pattern. `ats_config`: {} unless known.
- `description`: 3 to 5 sentences in your own words from what you know (what it is, who it serves, what the pediatric program includes, what the area is like). Mark uncertain details with "reportedly". A writing pass later refines it against the Wikipedia extract.
- `highlights`: 3 to 6 bullets you are confident about.
- `sources`: list any URL you actually read; otherwise ["model knowledge (to verify); enrichment pending"].
- `confidence`: "medium" when the hospital is well known to you and the pediatric facts are solid; "low" when inclusion rests on a state bed list or a single fact. Never "high" in this mode unless you read sources.
- Pediatric flags (`peds_inpatient`, `picu`, `peds_ed`, `nicu_level`): true / a level only when you are confident; otherwise false / null, and say in `notes` what should be verified (for example "DOH lists pediatric beds; unit size unknown").

Spend your web calls on: (1) borderline inclusion (does this 150-bed regional hospital still have an inpatient pediatric unit?), (2) hospitals you do not recognize, (3) recent closures or renames. One WebSearch per question, no WebFetch of long pages unless essential. Stop at 20 calls; after that, decide from the scratch data and mark confidence low.

## Mechanics
- Save incrementally with the helper: `python3 -I /home/claude/npjobs/scripts/append_hospital.py <region file> "<region_group>" <batch.json>` after every 3 to 5 records (batch.json may hold a list). Keep the existing region_group string of the file.
- Validate in seed mode: `python3 -I /home/claude/npjobs/scripts/validate.py --seed <region file>` (allows null coordinates). Fix every ERROR.
- Do not run git. No em dashes anywhere.

## Report (final message, under 400 words)
Counts added by hospital_type and region; the remaining enumeration you could not place (name, city, reason); hospitals you excluded and why; which records are confidence low and why; web calls used.
