#!/usr/bin/env python3
"""Append one or more hospital records to a region JSON file, keeping it valid.

Usage:
  python3 -I scripts/append_hospital.py data/regions/<file>.json "<region_group>" record.json [record2.json ...]
  (each record file holds one hospital object, or a list of them)
Records with a slug already present are replaced.
"""
import json, sys
from pathlib import Path

def main(a):
    out, group, recs = Path(a[1]), a[2], a[3:]
    doc = {"region_group": group, "hospitals": []}
    if out.exists() and out.stat().st_size > 0:
        doc = json.loads(out.read_text())
        doc.setdefault("hospitals", [])
    by_slug = {h.get("slug"): i for i, h in enumerate(doc["hospitals"])}
    n = 0
    for r in recs:
        data = json.loads(Path(r).read_text())
        for h in (data if isinstance(data, list) else [data]):
            if h.get("slug") in by_slug:
                doc["hospitals"][by_slug[h["slug"]]] = h
            else:
                by_slug[h.get("slug")] = len(doc["hospitals"])
                doc["hospitals"].append(h)
            n += 1
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(out)
    print(f"{out}: {len(doc['hospitals'])} hospitals ({n} written)")

if __name__ == "__main__":
    sys.exit(main(sys.argv))
