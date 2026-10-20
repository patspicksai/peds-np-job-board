#!/usr/bin/env python3
"""Merge data/regions/*.json into data/hospitals.json (the gold ledger).

Usage: python3 -I scripts/merge_hospitals.py [--out data/hospitals.json] [--keep-existing]

Rules:
- Region files are the source of truth for their slugs. The merged file keeps records in region order
  (hotspot regions first) then by name.
- --keep-existing: records already in the output file whose slug is not in any region file are kept
  (so manual additions survive a re-merge). Default: they are kept too, but flagged in the summary.
- Validates each region file first; refuses to merge a file with errors.
- Derives `hotspot` from region, and writes `generated_at`.
"""
import argparse
import datetime as dt
import glob
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from validate import REGIONS, check  # noqa: E402

HOTSPOT_ORDER = [r for r, h in REGIONS.items() if h] + [r for r, h in REGIONS.items() if not h]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "hospitals.json"))
    ap.add_argument("--regions", default=str(ROOT / "data" / "regions"))
    ap.add_argument("--allow-errors", action="store_true", help="merge even if validation reports errors")
    a = ap.parse_args()

    files = sorted(glob.glob(str(Path(a.regions) / "*.json")))
    files = [f for f in files if not f.endswith(".tmp")]
    if not files:
        print("no region files found")
        return 1
    merged = {}
    bad = 0
    for f in files:
        errs, warns = check(f)
        n = len(json.loads(Path(f).read_text()).get("hospitals", []))
        print(f"{Path(f).name}: {n} hospitals, {len(errs)} errors, {len(warns)} warnings")
        if errs and not a.allow_errors:
            for e in errs[:20]:
                print("   ERROR", e)
            bad += 1
            continue
        for h in json.loads(Path(f).read_text())["hospitals"]:
            h["hotspot"] = REGIONS.get(h.get("region"), False)
            if h["slug"] in merged:
                print(f"   WARN duplicate slug across files: {h['slug']} (keeping first)")
                continue
            merged[h["slug"]] = h
    if bad and not a.allow_errors:
        print(f"{bad} region file(s) failed validation; fix them or pass --allow-errors")
        return 1

    out = Path(a.out)
    kept_manual = 0
    if out.exists():
        try:
            old = json.loads(out.read_text())
            for h in old.get("hospitals", []):
                if h["slug"] not in merged:
                    merged[h["slug"]] = h
                    kept_manual += 1
        except Exception as e:  # noqa: BLE001
            print(f"WARN could not read existing {out}: {e}")

    order = {r: i for i, r in enumerate(HOTSPOT_ORDER)}
    hospitals = sorted(merged.values(), key=lambda h: (order.get(h.get("region"), 999), h.get("state", ""), h.get("name", "")))
    doc = {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "count": len(hospitals),
        "hospitals": hospitals,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    by_region = {}
    for h in hospitals:
        by_region[h.get("region")] = by_region.get(h.get("region"), 0) + 1
    print(f"wrote {out}: {len(hospitals)} hospitals ({kept_manual} kept from existing file, not in any region file)")
    for r in HOTSPOT_ORDER:
        if r in by_region:
            print(f"   {r}: {by_region[r]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
