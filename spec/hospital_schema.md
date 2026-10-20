# hospitals.json schema

Top level:

```json
{
  "generated_at": "2026-10-20T03:00:00Z",
  "hospitals": [ { ...hospital... } ]
}
```

Per-region research files (`data/regions/*.json`) use the same shape with an extra `"region_group": "R6 North Carolina"` key.

## Hospital object

Every field is present on every record. Use `null` when truly unknown, never omit the key and never guess a number. Strings are plain text (no markdown). All facts must come from sources you actually read; put the URLs you used in `sources`.

| field | type | rules |
|---|---|---|
| `slug` | string | kebab-case, unique, stable. `<short-name>-<city>` e.g. `maine-medical-center-portland`, `unc-childrens-chapel-hill`, `levine-childrens-charlotte`. Never change once assigned. |
| `name` | string | Official current name. If a children's hospital is inside a larger hospital, use the children's hospital name and put the parent in `parent_hospital`. |
| `parent_hospital` | string or null | e.g. `"Maine Medical Center"` for Barbara Bush Children's Hospital. |
| `health_system` | string or null | e.g. `"MaineHealth"`, `"UNC Health"`, `"Atrium Health"`, `"Hackensack Meridian Health"`. |
| `hospital_type` | enum | one of `childrens_hospital` (freestanding), `childrens_hospital_within_hospital`, `academic_medical_center`, `regional_hospital`, `community_hospital`, `critical_access`, `specialty_pediatric` (rehab, psych, specialty). |
| `city` | string | |
| `state` | string | two-letter |
| `address` | string | full street address |
| `lat` | number | WGS84, 5 decimals, must land on the hospital building |
| `lng` | number | negative for the US |
| `coord_source` | string | where lat/lng came from (Wikipedia infobox, hospital site, OpenStreetMap, Google Places) |
| `region` | string | exactly one value from spec/regions.md |
| `hotspot` | boolean | derived from region (see regions.md) |
| `website` | string | main hospital site |
| `careers_url` | string | the health system's careers / job search landing page (the page where jobs are actually listed, not a marketing page) |
| `careers_platform` | enum | `workday`, `icims`, `taleo`, `successfactors`, `phenom`, `jobvite`, `ultipro`, `greenhouse`, `lever`, `smartrecruiters`, `custom`, `unknown` |
| `careers_search_url` | string or null | A URL that, when opened, lists nurse practitioner jobs at this system. Prefer a search-results URL with the query embedded (e.g. `...?q=nurse+practitioner`, `...search?keywords=nurse%20practitioner`). If the platform cannot encode a query in the URL, use the base job-search URL. Test it by fetching it once; note the result in `careers_notes`. |
| `careers_notes` | string or null | anything useful for the scraper: "Workday, listings are JS-rendered, use search fallback"; "iCIMS, search page renders server-side"; "jobs posted under parent system Novant Health". |
| `ats_config` | object | identifiers the automated scraper needs, keyed by platform. Fill what you can confirm from the careers URL itself. Workday: `{"host": "mainehealth.wd1.myworkdayjobs.com", "tenant": "mainehealth", "site": "MaineHealth"}` (from a URL like `https://mainehealth.wd1.myworkdayjobs.com/en-US/MaineHealth`, tenant is the subdomain before `.wd1`, site is the path segment after the locale). iCIMS: `{"host": "careers-xyz.icims.com"}`. Oracle Cloud HCM: `{"host": "xxxx.fa.us2.oraclecloud.com", "site_number": "CX_1"}`. SuccessFactors: `{"host": "careers.xyz.org"}`. Phenom: `{"host": "careers.xyz.org"}`. UKG/UltiPro: `{"host": "recruiting.ultipro.com", "board_path": "/COMPANY1234/JobBoard/<guid>"}`. Eightfold: `{"host": "xyz.eightfold.ai"}`. Taleo: `{"host": "xyz.taleo.net", "career_section": "ext"}`. Custom/unknown: `{"host": "..."}`. Empty object `{}` if nothing confirmable. |
| `nearest_airport` | string or null | e.g. `"PWM Portland International Jetport (15 min)"` |
| `beds` | integer or null | licensed or staffed beds for the whole hospital |
| `peds_beds` | integer or null | pediatric inpatient beds |
| `peds_inpatient` | boolean | has an inpatient pediatric unit (general peds floor) |
| `picu` | boolean | has a pediatric ICU |
| `picu_beds` | integer or null | |
| `nicu_level` | integer or null | 1 to 4 per AAP levels; null if no NICU or unknown |
| `peds_ed` | boolean | dedicated pediatric emergency department or peds ED area |
| `peds_trauma_level` | string or null | e.g. `"Level I pediatric trauma center"` |
| `magnet` | boolean or null | ANCC Magnet designation |
| `teaching` | boolean | residency / academic affiliation |
| `affiliated_university` | string or null | |
| `peds_services` | string[] | pediatric service lines, e.g. `["cardiology", "cardiac surgery", "hematology/oncology", "neurosurgery", "hospitalist", "transport team"]` |
| `description` | string | 3 to 6 sentences in your own words: what the hospital is, who it serves, what the pediatric program is known for, what the campus and surrounding area are like. No copied sentences from any source. |
| `highlights` | string[] | 3 to 8 short bullets: rankings, designations, notable programs, size, recent expansions, culture notes, "only children's hospital in the state", etc. |
| `photos` | object[] | each `{ "url": "...", "caption": "...", "credit": "...", "license": "...", "source_page": "..." }`. Prefer Wikimedia Commons: find the file on the hospital's Wikipedia article and use `https://commons.wikimedia.org/wiki/Special:FilePath/<File name with spaces>?width=1200` (URL-encode spaces as %20). Second choice: an image URL from the hospital's own newsroom / media page (check it is a direct image URL ending in .jpg/.png/.webp). Aim for 1 to 4 photos. Empty array if none found. |
| `wikipedia_title` | string or null | exact article title, e.g. `"Maine Medical Center"`. The page fetches the article summary and lead image live from this. |
| `sources` | string[] | every URL you read for this record |
| `notes` | string or null | anything else relevant to the job search: commute realities, nearby towns, hiring patterns, union, known NP roles |
| `confidence` | enum | `high`, `medium`, `low` for the record overall |
| `active` | boolean | always `true` on creation |

## Inclusion rules (be exhaustive)

Include every hospital in your states that meets ANY of:
1. Freestanding children's hospital or children's hospital within a hospital.
2. Any hospital with an inpatient pediatric unit, PICU, pediatric ED, or NICU (level II or higher).
3. Academic medical centers and large regional referral centers (they almost always have pediatrics).
4. In hotspot regions (see regions.md): every acute care hospital, even without a dedicated peds unit (mark `peds_inpatient: false`). The user may consider peds ED, NICU or mixed roles there and wants the full local picture.
5. Pediatric specialty hospitals (rehab, psych) where an acute care PNP could work.

Exclude: VA hospitals, military hospitals (note them in your report instead), surgical centers, standalone urgent cares, nursing facilities.

How to be exhaustive: start from the Children's Hospital Association member list, the state hospital association member directory, the state Department of Health facility list, Wikipedia's "List of hospitals in <state>", the AHA guide, and each health system's "locations" page. Then search `"<city>" hospital pediatric unit` for every city over ~25k people in your states. Do not stop at the famous names. A ledger that misses a 150-bed regional hospital with a 12-bed peds unit has failed.

## Geocoding

Get coordinates from, in order: the Wikipedia article (coordinates at the top right of the infobox), the hospital's own "directions / location" page, OpenStreetMap / Nominatim, the places search tool if you have one. Verify the point is on the hospital, not the city centroid. Record the source.

## Validation (scripts/validate.py enforces)

- all required keys present, enums valid, state two letters, region in list
- lat between 24 and 48, lng between -83 and -66
- slug unique, careers_url and website start with http
- no two records within 150 m of each other with similar names (dupe check)
