#!/usr/bin/env python3
"""Build data/coverage.json: per-hospital scrape health, derived from the run files the engine writes.

The board shows this as the "Coverage" view so the owners can see each day which boards were read, which
were not, why, and for how long, and work on the gaps for the next run.

Usage: python3 scripts/build_coverage.py [run_file.json] [--out data/coverage.json] [--ledger data/hospitals.json]
Defaults to the newest file in data/runs/.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CLASSES = [
    ("bot_block", re.compile(r"403|cloudflare|attention required|access denied|captcha|challenge|bot", re.I)),
    ("rate_limited", re.compile(r"429|too many requests", re.I)),
    ("dead_url", re.compile(r"404|not found|ERR_NAME_NOT_RESOLVED|does not resolve|no page reachable: None", re.I)),
    ("no_adapter", re.compile(r"board_path unknown|cannot derive host/tenant|no adapter|infor|inforcloudsuite|not implemented", re.I)),
    ("zero_results", re.compile(r"zero_results|returned 0 listings", re.I)),
    ("no_listings", re.compile(r"no_listings|no job listings recognized", re.I)),
    ("budget", re.compile(r"budget exhausted|timed out|timeout", re.I)),
]


def classify(status: str, error: str) -> str:
    text = f"{status} {error or ''}"
    if status == "ok":
        return "ok"
    for name, rx in CLASSES:
        if rx.search(text):
            return name
    return "other"


def hint(cls: str) -> str:
    return {
        "ok": "",
        "bot_block": "Site blocks automated clients. Options: TLS-impersonating client, stealth browser, sitemap, or a proxy key.",
        "rate_limited": "Site rate-limits. Slow the per-host throttle or move this board later in the run.",
        "dead_url": "Careers URL is wrong or gone. Rediscover the careers page from the hospital homepage and fix the ledger.",
        "no_adapter": "The job board vendor has no adapter yet, or the board id is missing from ats_config.",
        "zero_results": "Board reachable but the search returned nothing. Check the search URL, site id, or keyword encoding.",
        "no_listings": "Careers URL is a marketing page, not the job board. Find the real board (follow 'search jobs') and set it in the ledger.",
        "budget": "Run budget ran out before this board. Raise the budget or make earlier boards cheaper.",
        "other": "See the error text.",
    }.get(cls, "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_file", nargs="?")
    ap.add_argument("--out", default=str(ROOT / "data" / "coverage.json"))
    ap.add_argument("--ledger", default=str(ROOT / "data" / "hospitals.json"))
    ap.add_argument("--prev", default="", help="previous coverage.json to carry streaks from (default: the --out file)")
    a = ap.parse_args()

    run_file = a.run_file
    if not run_file:
        files = sorted(glob.glob(str(ROOT / "data" / "runs" / "*.json")))
        if not files:
            print("no run files")
            return 1
        run_file = files[-1]
    run = json.loads(Path(run_file).read_text())
    ledger = {h["slug"]: h for h in json.loads(Path(a.ledger).read_text()).get("hospitals", [])}
    prev = {}
    out_path = Path(a.out)
    prev_path = Path(a.prev) if a.prev else out_path
    if prev_path.exists():
        try:
            prev = {r["slug"]: r for r in json.loads(prev_path.read_text()).get("hospitals", [])}
        except Exception:  # noqa: BLE001
            prev = {}

    finished = run.get("finished_at") or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for r in run.get("hospitals", []):
        slug = r.get("slug")
        h = ledger.get(slug, {})
        status = r.get("status") or ("ok" if r.get("listings") else "error")
        err = (r.get("error") or "")[:400]
        cls = classify(status, err)
        p = prev.get(slug, {})
        ok = cls == "ok"
        row = {
            "slug": slug,
            "name": h.get("name") or r.get("name"),
            "region": h.get("region"),
            "hotspot": bool(h.get("hotspot")),
            "health_system": h.get("health_system"),
            "careers_platform": h.get("careers_platform"),
            "careers_url": h.get("careers_search_url") or h.get("careers_url"),
            "adapter": r.get("adapter") or r.get("platform"),
            "method": r.get("method"),
            "board": r.get("board"),
            "board_hospitals": r.get("board_hospitals"),
            "status": "ok" if ok else status,
            "class": cls,
            "error": "" if ok else err,
            "hint": hint(cls),
            "listings": r.get("listings", 0),
            "candidates": r.get("candidates", 0),
            "new": r.get("new", 0),
            "last_checked_at": finished,
            "last_ok_at": finished if ok else p.get("last_ok_at"),
            "consecutive_failures": 0 if ok else (p.get("consecutive_failures", 0) + 1),
            "first_failed_at": None if ok else (p.get("first_failed_at") or finished),
            "careers_notes": (h.get("careers_notes") or "")[-300:],
        }
        rows.append(row)
    # hospitals in the ledger that the run never reached keep their previous row. A partial run (--only a few
    # slugs, or a retry) covers well under half the ledger: those rows stay exactly as they were. A full run that
    # ran out of budget marks them unchecked so the gap is visible.
    seen = {r["slug"] for r in rows}
    active = [slug for slug, h in ledger.items() if h.get("active", True)]
    partial = len(rows) < 0.5 * max(1, len(active))
    for slug, h in ledger.items():
        if slug in seen or not h.get("active", True):
            continue
        p = prev.get(slug, {})
        if partial and p:
            rows.append(dict(p))
            continue
        rows.append({**{k: p.get(k) for k in p}, "slug": slug, "name": h.get("name"), "region": h.get("region"),
                     "hotspot": bool(h.get("hotspot")), "health_system": h.get("health_system"),
                     "careers_platform": h.get("careers_platform"), "status": "unchecked", "class": "budget",
                     "error": "not reached in this run", "hint": hint("budget"), "last_checked_at": p.get("last_checked_at"),
                     "consecutive_failures": p.get("consecutive_failures", 0) + 1, "careers_notes": (h.get("careers_notes") or "")[-300:]})
    rows.sort(key=lambda r: (r["class"] == "ok", not r["hotspot"], -(r.get("consecutive_failures") or 0), r["name"] or ""))
    by_class = {}
    for r in rows:
        by_class[r["class"]] = by_class.get(r["class"], 0) + 1
    doc = {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run.get("run_id"),
        "run_started_at": run.get("started_at"),
        "run_finished_at": finished,
        "totals": {**run.get("totals", {}), "hospitals": len(rows), "checked": sum(1 for r in rows if r["class"] == "ok"),
                   "unreachable": sum(1 for r in rows if r["class"] != "ok"), **({"partial": True} if partial else {})},
        "by_class": by_class,
        "hotspot_not_ok": sum(1 for r in rows if r["hotspot"] and r["class"] != "ok"),
        "hospitals": rows,
    }
    out_path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {out_path}: {len(rows)} hospitals, {by_class}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
