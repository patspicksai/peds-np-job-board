#!/usr/bin/env python3
"""Merge scrape run files into data/jobs.json.

Usage:
  python3 scripts/merge_jobs.py                       # merge today's data/runs/<date>*.json
  python3 scripts/merge_jobs.py data/runs/2026-10-20.json [more run files...]
  python3 scripts/merge_jobs.py --all                 # every data/runs/*.json and data/jobs_runs/*.json (idempotent)
Options: --jobs PATH (default data/jobs.json), --dry-run, --stats-out PATH (JSON: counts + new jobs, for notify)

Rules (spec/jobs_schema.md): insert new normalized URLs with first_seen_at; bump last_seen_at for known ones;
mark stale after 7 days unseen while the hospital was reachable; closed when the run reports it.
Never deletes. Refuses to write (exit 3) if the active job count would drop below 50% of the previous count,
or if the existing jobs.json does not parse (exit 4).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scraper import merge as m  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="*")
    ap.add_argument("--jobs", default=str(ROOT / "data" / "jobs.json"))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stats-out", default="")
    a = ap.parse_args(argv)

    if a.runs:
        files = [Path(p) for p in a.runs]
    elif a.all:
        files = sorted((ROOT / "data" / "runs").glob("*.json")) + sorted((ROOT / "data" / "jobs_runs").glob("*.json"))
    else:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        files = sorted((ROOT / "data" / "runs").glob(f"{today}*.json"))
    if not files:
        print("merge: no run files to merge")
        return 0
    runs = []
    for f in files:
        try:
            r = json.loads(f.read_text())
        except Exception as e:  # noqa: BLE001
            print(f"merge: skipping unreadable run file {f}: {e}", file=sys.stderr)
            continue
        if r.get("dry_run"):
            print(f"merge: skipping dry-run file {f}")
            continue
        runs.append(r)
    jobs_path = Path(a.jobs)
    try:
        existing = m.load_jobs(jobs_path)
    except Exception as e:  # noqa: BLE001
        print(f"merge: existing {jobs_path} does not parse ({e}); refusing to write", file=sys.stderr)
        return 4
    try:
        doc, stats = m.merge(existing, runs)
    except m.SafetyError as e:
        print(f"merge: SAFETY STOP: {e}", file=sys.stderr)
        if a.stats_out:
            Path(a.stats_out).write_text(json.dumps({"error": str(e), "new_jobs": []}))
        return 3
    new_jobs = stats.pop("new_jobs")
    print(f"merge: {len(files)} run file(s); new={stats['new']} updated={stats['updated']} stale={stats['stale']} "
          f"closed={stats['closed']} reopened={stats['reopened']} active {stats['prev_active']} -> {stats['active']}")
    if a.stats_out:
        Path(a.stats_out).write_text(json.dumps({**stats, "new_jobs": [
            {k: j.get(k) for k in ("id", "hospital_slug", "title", "url", "relevance", "setting", "posted_at")}
            for j in new_jobs]}, indent=1))
    if a.dry_run:
        return 0
    m.write_jobs(jobs_path, doc)
    print(f"merge: wrote {jobs_path} ({len(doc['jobs'])} jobs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
