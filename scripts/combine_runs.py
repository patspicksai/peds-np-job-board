#!/usr/bin/env python3
"""Overlay a retry run onto the day's base run file.

The scrape workflow runs the full ledger once, then re-runs the boards that failed for transient reasons (bot walls,
rate limits, timeouts, connection errors) from a fresh runner. This script folds that retry back into the base run
file so data/runs/<date>.json, coverage.json and jobs.json last_run describe the day as a whole:

- per-hospital status rows for the retried boards are replaced by the retry's rows
- checked / unreachable / unreachable_detail / totals are recomputed from the overlaid rows
- the retry's full job records are appended (the base file is usually compacted to a job index by then), its
  seen_again and closed lists are unioned, and the result is marked not compacted so scripts/merge_jobs.py inserts
  the retry's new jobs and treats the base index as "seen again"
- finished_at becomes the retry's finish; a "retry" block records what was retried and what it fixed

Usage: python3 scripts/combine_runs.py BASE.json RETRY.json [--out BASE.json]
Also: python3 scripts/combine_runs.py --select BASE.json   (prints the comma-separated slugs worth retrying)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_coverage import classify  # noqa: E402

# failure classes a fresh runner can plausibly fix; no_listings / no_adapter / zero_results need code or ledger work
RETRY_CLASSES = {"bot_block", "rate_limited", "budget", "other"}
TRANSIENT_RE = re.compile(r"no page reachable: None|ConnectTimeout|ReadTimeout|TimeoutError|ConnectionError|RemoteDisconnected|"
                          r"ERR_CONNECTION|ERR_TIMED_OUT|net::ERR_|HTTP 5\d\d|\b50[0-9]\b", re.I)


def retry_slugs(run: dict, prev_coverage: dict | None = None) -> list[str]:
    """Boards worth a second pass: transient failure classes, plus any board that was read OK in the previous
    coverage file but not in this run (a different ~35 boards flip each run: bot challenges served as HTML to an
    API adapter, a listing page that came back empty once, a 403 on a site that answered the day before)."""
    prev_ok = set()
    for r in (prev_coverage or {}).get("hospitals", []) or []:
        if r.get("class") == "ok" or r.get("status") == "ok":
            prev_ok.add(r.get("slug"))
    out = []
    for r in run.get("hospitals", []):
        if r.get("status") == "ok":
            continue
        cls = classify(r.get("status") or "", r.get("error") or "")
        if cls in RETRY_CLASSES or (cls == "dead_url" and TRANSIENT_RE.search(r.get("error") or "")) \
                or r["slug"] in prev_ok:
            out.append(r["slug"])
    return out


def combine(base: dict, retry: dict) -> dict:
    rows = {r["slug"]: r for r in base.get("hospitals", [])}
    retried = [r["slug"] for r in retry.get("hospitals", [])]
    fixed = []
    for r in retry.get("hospitals", []):
        before = rows.get(r["slug"], {}).get("status")
        r = dict(r)
        r["notes"] = (r.get("notes") or [])[:10] + ["retried from a fresh runner"]
        rows[r["slug"]] = r
        if before != "ok" and r.get("status") == "ok":
            fixed.append(r["slug"])
    hospitals = list(rows.values())
    checked = [r["slug"] for r in hospitals if r.get("status") == "ok"]
    unreachable = [r["slug"] for r in hospitals if r.get("status") != "ok"]
    base_jobs = list(base.get("jobs") or [])
    seen_extra: list[str] = []
    if base.get("compacted"):
        # the base file holds only a job index by now (its records already live in jobs.json): keep them as
        # "seen again" so the merge never inserts stubs, and carry only the retry's full records as jobs
        seen_extra = [j.get("url") for j in base_jobs if j.get("url")]
        jobs: list[dict] = []
        have = set(seen_extra) | {j.get("id") for j in base_jobs}
    else:
        jobs = base_jobs
        have = {j.get("id") for j in jobs} | {j.get("url") for j in jobs}
    added = 0
    for j in retry.get("jobs") or []:
        if j.get("id") in have or j.get("url") in have:
            continue
        jobs.append(j)
        added += 1
    base_new = int((base.get("totals") or {}).get("new_candidates") or len(base_jobs))
    out = dict(base)
    out.update({
        "hospitals": hospitals, "checked": checked, "unreachable": unreachable,
        "unreachable_detail": {r["slug"]: r.get("error") for r in hospitals if r.get("status") != "ok"},
        "jobs": jobs, "job_index": base_jobs if base.get("compacted") else [],
        "seen_again": sorted(set(base.get("seen_again") or []) | set(retry.get("seen_again") or []) | set(seen_extra)),
        "closed": sorted(set(base.get("closed") or []) | set(retry.get("closed") or [])),
        "finished_at": retry.get("finished_at") or base.get("finished_at"),
        "compacted": False,
        "retry": {"started_at": retry.get("started_at"), "finished_at": retry.get("finished_at"),
                  "retried": retried, "fixed": fixed, "jobs_added": added,
                  "wall_seconds": retry.get("wall_seconds")},
        "totals": {
            "hospitals": len(hospitals), "checked": len(checked), "unreachable": len(unreachable),
            "listings": sum({r.get("board") or r["slug"]: int(r.get("board_listings") or 0) for r in hospitals}.values()),
            "candidates": (base.get("totals") or {}).get("candidates", 0) + (retry.get("totals") or {}).get("candidates", 0),
            "new_candidates": base_new + added,
            "seconds": round(float((base.get("totals") or {}).get("seconds", 0)) + float((retry.get("totals") or {}).get("seconds", 0)), 1),
        },
    })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("retry", nargs="?")
    ap.add_argument("--out", default="")
    ap.add_argument("--select", action="store_true", help="print the slugs of the base run worth retrying and exit")
    ap.add_argument("--prev-coverage", default="", help="previous coverage.json; boards it lists as OK are retried too")
    a = ap.parse_args()
    base = json.loads(Path(a.base).read_text())
    if a.select:
        prev = None
        if a.prev_coverage and Path(a.prev_coverage).exists():
            try:
                prev = json.loads(Path(a.prev_coverage).read_text())
            except Exception:  # noqa: BLE001
                prev = None
        print(",".join(retry_slugs(base, prev)))
        return 0
    if not a.retry:
        ap.error("RETRY.json is required unless --select")
    retry = json.loads(Path(a.retry).read_text())
    out = combine(base, retry)
    path = Path(a.out or a.base)
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    t = out["totals"]
    print(f"combined: {len(out['retry']['retried'])} boards retried, {len(out['retry']['fixed'])} fixed, "
          f"{out['retry']['jobs_added']} jobs added; now ok={t['checked']} not_ok={t['unreachable']} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
