"""CLI: python -m scraper.run [--ledger PATH] [--only slug,slug] [--platform workday] [--limit N]
                              [--no-details] [--dry-run] [--out PATH] [--report-dir DIR] [--budget SECONDS]

Writes data/runs/<YYYY-MM-DD>.json (per-hospital status + candidate Job records) unless --dry-run,
plus a human-readable status table. Merge into data/jobs.json with scripts/merge_jobs.py.
Exit code: 0 normally (individual board failures are reported, not fatal); 2 on bad arguments / ledger.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from . import config, detect, pipeline
from .models import normalize_url

log = logging.getLogger("scraper")


def load_ledger(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    hs = data.get("hospitals") if isinstance(data, dict) else data
    if not isinstance(hs, list):
        raise ValueError(f"{path}: expected {{'hospitals': [...]}}")
    return [h for h in hs if h.get("active", True) and h.get("slug")]


def load_known(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return {}
    return {normalize_url(j["url"]): j for j in doc.get("jobs", []) if j.get("url")}


def status_table(run: dict) -> str:
    rows = run["hospitals"]
    w = max([len(r["slug"]) for r in rows] + [8])
    lines = [f"{'hospital'.ljust(w)}  {'platform':<22} {'method':<16} {'board':>5} {'list':>5} {'cand':>5} {'new':>4} {'secs':>6}  status / error",
             "-" * (w + 100)]
    for r in sorted(rows, key=lambda r: (r["status"] != "ok", r["slug"])):
        err = (r["error"] or "")[:150]
        lines.append(f"{r['slug'].ljust(w)}  {str(r['platform'])[:22]:<22} {str(r['method'])[:16]:<16} {r['board_listings']:>5} "
                     f"{r['listings']:>5} {r['candidates']:>5} {r['new']:>4} {r['seconds']:>6}  {r['status']}{(': ' + err) if err else ''}")
    t = run["totals"]
    lines.append("-" * (w + 100))
    lines.append(f"hospitals={t['hospitals']} ok={t['checked']} not_ok={t['unreachable']} board_listings={t['listings']} "
                 f"candidates={t['candidates']} new_candidates={t['new_candidates']}")
    return "\n".join(lines)


def samples_report(run: dict) -> str:
    out = []
    for r in sorted(run["hospitals"], key=lambda r: r["slug"]):
        out.append(f"## {r['slug']}  [{r['platform']} / {r['adapter']} / {r['method']}]  status={r['status']}")
        if r["error"]:
            out.append(f"   ERROR: {r['error']}")
        for n in r.get("notes") or []:
            out.append(f"   note: {n[:300]}")
        if r["samples"]:
            for s in r["samples"]:
                out.append(f"   * [{s.get('relevance')}/{s.get('setting')}] {s['title']} | posted {s.get('posted_at')} | {s['url']}"
                           + ("  (known)" if s.get("known") else ""))
        else:
            out.append("   (no candidate jobs)")
        if r.get("sample_listings"):
            out.append("   first board listings: " + " || ".join(f"{x['title']} ({x.get('location', '')})" for x in r["sample_listings"]))
        out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scraper.run")
    ap.add_argument("--ledger", default=str(config.LEDGER))
    ap.add_argument("--jobs", default=str(config.JOBS), help="existing jobs.json (known URLs are not re-fetched)")
    ap.add_argument("--only", default="", help="comma-separated hospital slugs")
    ap.add_argument("--platform", default="", help="only hospitals whose detected platform matches")
    ap.add_argument("--limit", type=int, default=0, help="max hospitals")
    ap.add_argument("--no-details", action="store_true", help="skip posting detail fetches")
    ap.add_argument("--dry-run", action="store_true", help="do not write data/runs/<date>.json")
    ap.add_argument("--out", default="", help="run file path (default data/runs/<date>.json)")
    ap.add_argument("--report-dir", default="", help="also write status.txt / samples.txt / run.json here")
    ap.add_argument("--budget", type=int, default=config.RUN_BUDGET_SECONDS)
    ap.add_argument("--max-details", type=int, default=config.MAX_DETAILS_PER_HOSPITAL)
    ap.add_argument("--keywords", default="", help="override keyword list (comma-separated)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        hospitals = load_ledger(Path(a.ledger))
    except Exception as e:  # noqa: BLE001
        print(f"error: cannot load ledger {a.ledger}: {e}", file=sys.stderr)
        return 2
    if a.only:
        want = {s.strip() for s in a.only.split(",") if s.strip()}
        hospitals = [h for h in hospitals if h["slug"] in want]
    if a.platform:
        hospitals = [h for h in hospitals if detect.detect_platform(h) == a.platform or h.get("careers_platform") == a.platform]
    if a.limit:
        hospitals = hospitals[: a.limit]
    if not hospitals:
        print("error: no hospitals selected", file=sys.stderr)
        return 2

    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    opts = SimpleNamespace(
        keywords=[k.strip() for k in a.keywords.split(",") if k.strip()] or list(config.KEYWORDS),
        no_details=a.no_details, dry_run=a.dry_run, budget=a.budget, max_details=a.max_details, run_id=run_id)
    log.info("scraping %d hospitals (%d boards), budget %ss", len(hospitals), len(pipeline.group_boards(hospitals)), a.budget)
    t0 = time.time()
    run = pipeline.run(hospitals, load_known(Path(a.jobs)), opts)
    run["ledger"] = a.ledger
    run["wall_seconds"] = round(time.time() - t0, 1)
    table = status_table(run)
    print(table)
    if a.report_dir:
        d = Path(a.report_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "status.txt").write_text(f"run {run_id} wall={run['wall_seconds']}s ledger={a.ledger}\n\n{table}\n")
        (d / "samples.txt").write_text(samples_report(run))
        (d / "run.json").write_text(json.dumps(run, indent=1, ensure_ascii=False))
    if not a.dry_run:
        out = Path(a.out) if a.out else config.RUNS / f"{run_id}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            # second run of the day: keep both, merge reads all
            out = out.with_name(f"{run_id}_{datetime.now(timezone.utc).strftime('%H%M')}.json")
        out.write_text(json.dumps(run, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {out}")
        if a.report_dir:
            (Path(a.report_dir) / "run_path.txt").write_text(str(out) + "\n")
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # Boards abandoned at the hard budget may still hold worker threads; the interpreter would wait for them
    # at exit (ThreadPoolExecutor threads are joined by an atexit hook). The run file is already written.
    os._exit(code)
