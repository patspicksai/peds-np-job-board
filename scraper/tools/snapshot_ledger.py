"""Build a test ledger from research files (data/regions/*.json or data/hospitals.json).

Usage: python -m scraper.tools.snapshot_ledger <in.json> [more...] -o OUT [--one-per-board] [--exclude ledger.json]
                                               [--limit N] [--platform P]

--one-per-board keeps one hospital per job board (detect.board_key), which is what platform-coverage tests need:
system-wide boards are scraped once per run anyway. Unreadable / partial files are skipped with a warning.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import detect

MINIMAL = ("slug", "name", "parent_hospital", "health_system", "hospital_type", "city", "state", "region", "hotspot",
           "website", "careers_url", "careers_platform", "careers_search_url", "careers_notes", "ats_config", "active")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--one-per-board", action="store_true")
    ap.add_argument("--exclude", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--platform", default="")
    ap.add_argument("--minimal", action="store_true", help="keep only the fields the scraper reads")
    a = ap.parse_args(argv)
    skip: set[str] = set()
    if a.exclude and Path(a.exclude).exists():
        skip = {h.get("slug") for h in json.loads(Path(a.exclude).read_text()).get("hospitals", [])}
    seen, boards, out = set(), set(), []
    for p in a.inputs:
        try:
            doc = json.loads(Path(p).read_text())
        except Exception as e:  # noqa: BLE001
            print(f"snapshot: skipping {p}: {e}", file=sys.stderr)
            continue
        for h in doc.get("hospitals", []) if isinstance(doc, dict) else []:
            slug = h.get("slug")
            if not slug or slug in seen or slug in skip or not (h.get("careers_url") or h.get("careers_search_url")):
                continue
            plat = detect.detect_platform(h)
            if a.platform and plat != a.platform:
                continue
            key = detect.board_key(h, plat)
            if a.one_per_board and key in boards:
                continue
            seen.add(slug)
            boards.add(key)
            h = {k: h.get(k) for k in MINIMAL} if a.minimal else dict(h)
            h["active"] = True if h.get("active") is None else h["active"]
            out.append(h)
    if a.limit:
        out = out[: a.limit]
    Path(a.out).write_text(json.dumps({"generated_at": None, "hospitals": out}, indent=1))
    print(f"snapshot: {len(out)} hospitals ({len(boards)} boards) -> {a.out}")
    return 0 if out else 1


if __name__ == "__main__":
    raise SystemExit(main())
