# jobs.json schema

```json
{
  "generated_at": "2026-10-20T10:15:00Z",
  "last_run": {
    "run_id": "2026-10-20",
    "started_at": "...",
    "finished_at": "...",
    "hospitals_checked": 128,
    "hospitals_unreachable": ["slug", "slug"],
    "new_jobs": 7,
    "updated_jobs": 41,
    "stale_jobs": 2,
    "notes": "free text"
  },
  "jobs": [ { ...job... } ]
}
```

Per-run scrape files (`data/jobs_runs/<run_id>_<region>.json`) are `{ "run_id": "...", "region_group": "...", "checked": ["slug", ...], "unreachable": ["slug", ...], "jobs": [ ...job... ] }`.

## Job object

| field | type | rules |
|---|---|---|
| `id` | string | first 12 hex chars of sha1 of the normalized `url` (lowercase, strip query string tracking params `utm_*`, `src`, `source`, `ref`, trailing slash). Stable across runs. scripts/merge_jobs.py computes this; agents may leave it null. |
| `hospital_slug` | string | must match a slug in hospitals.json. If a posting is at a system level and could be any of several hospitals, pick the one named in the posting's location; if none, pick the system's flagship and say so in `relevance_reason`. |
| `title` | string | exact posting title |
| `url` | string | the EMPLOYER's own posting URL (Workday, iCIMS, etc). Never an Indeed / LinkedIn / ZipRecruiter URL unless the employer page truly cannot be found, in which case set `source` accordingly. |
| `apply_url` | string or null | if different from url |
| `source` | enum | `employer`, `indeed`, `linkedin`, `glassdoor`, `ziprecruiter`, `other_aggregator` |
| `external_id` | string or null | the employer's requisition / job ID (e.g. `R123456`, `2024-12345`) |
| `location_text` | string | location as written in the posting |
| `city` | string or null | |
| `state` | string or null | two letters |
| `department` | string or null | e.g. `"Pediatric Intensive Care Unit"`, `"Pediatric Hospital Medicine"` |
| `setting` | enum | best guess from the posting: `picu`, `nicu`, `peds_inpatient`, `peds_ed`, `peds_specialty_inpatient` (cardiology, surgery, onc inpatient consult services), `peds_specialty_outpatient`, `peds_primary_care`, `mixed`, `unspecified` |
| `employment_type` | string or null | Full time, Part time, Per diem, PRN |
| `shift` | string or null | Days, Nights, Rotating, 12-hour, etc |
| `schedule` | string or null | e.g. `"3x12s"`, `"7 on / 7 off"` |
| `salary_text` | string or null | exactly as posted; null if not posted |
| `posted_at` | string or null | ISO date if the posting shows one; else null (do not invent) |
| `summary` | string | 2 to 3 sentences in your own words: the role, the unit, the patient population, anything notable (new program, sign-on bonus, relocation help). |
| `description` | string | the posting's full job description text, cleaned of navigation / boilerplate, max ~6000 characters (truncate with `...` and keep the URL for the rest). This is the only field where copying the source text is correct: it is the job posting itself and the user needs it verbatim. |
| `requirements` | string[] | bullets pulled from the posting: certifications (CPNP-AC, CPNP-PC, FNP accepted?), experience years, license, BLS/PALS |
| `relevance` | integer | 3 = explicitly pediatric AND acute / inpatient / PICU / NICU / peds ED / peds hospitalist / peds inpatient specialty. 2 = pediatric NP where setting is unclear or outpatient specialty at a children's hospital, OR acute care NP at a children's hospital. 1 = NP role at a hospital in the ledger that is not clearly pediatric (e.g. "Nurse Practitioner - Critical Care" at a general hospital with a peds unit) or an adult role we keep only for context. 0 = not relevant (do not include). |
| `relevance_reason` | string | one sentence |
| `tags` | string[] | free-form: `"sign-on bonus"`, `"new grad friendly"`, `"nights"`, `"CPNP-AC required"`, `"FNP accepted"`, `"relocation assistance"`, `"union"` |
| `first_seen_at` | string | ISO timestamp (merge script sets on insert) |
| `last_seen_at` | string | ISO timestamp (merge script bumps each run the job is seen) |
| `status` | enum | `active`, `stale` (not seen for 7+ days while the hospital was reachable), `closed` (posting page says filled / 404) |
| `raw_notes` | string or null | anything the scraper wants to pass along |

## Relevance filter for the scrape

Keep a posting if the title or description contains any of: pediatric, pediatrics, peds, children's, PICU, NICU, neonatal, PNP, CPNP, "acute care" together with any pediatric term, "pediatric hospitalist", "pediatric critical care", "pediatric cardiology", "pediatric surgery", "pediatric emergency". AND it is an advanced practice role: nurse practitioner, NP, APP, advanced practice provider, APRN, advanced practice registered nurse, CRNP (PA-only roles excluded unless the posting says "NP or PA").

Also keep relevance-1 roles ONLY when the hospital is in a hotspot region.
