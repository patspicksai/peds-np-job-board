#!/usr/bin/env python3
"""Patch hospital records by slug in data/hospitals.json AND the data/regions/*.json file that holds the slug.

Patch file (JSON): {"<slug>": {"set": {field: value, ...},
                               "ats_config": {...},      # replaces ats_config; KEEP_CFG keys of the old one survive
                               "note": "...",            # appended to careers_notes
                               "sources": ["..."]}}      # appended to sources when missing

Writes are atomic (temp file in the same directory, then os.replace) and keep the repo's JSON style
(indent=2, ensure_ascii=False, trailing newline). Nothing is written if any slug is unknown or a value is invalid.

Usage: python3 -I scripts/patch_hospitals.py <patch.json> [--dry-run]
"""
from __future__ import annotations

import glob
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "data" / "hospitals.json"
REGIONS = ROOT / "data" / "regions"
KEEP_CFG = ("location_aliases", "flagship", "provider_recruiting_host", "nicu_employer_ats")
SETTABLE = {"careers_platform", "careers_url", "careers_search_url"}
PLATFORMS = {"workday", "icims", "taleo", "successfactors", "phenom", "jobvite", "ultipro", "greenhouse",
             "lever", "smartrecruiters", "oracle_cloud", "eightfold", "pageup", "cornerstone", "adp",
             "avature", "dayforce", "paycom", "custom", "unknown"}


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def dump_atomic(p: Path, doc: dict) -> None:
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def check_patch(slug: str, pt: dict) -> list[str]:
    errs = []
    for k in pt.get("set", {}):
        if k not in SETTABLE:
            errs.append(f"{slug}: field {k!r} is not patchable here")
    s = pt.get("set", {})
    if "careers_platform" in s and s["careers_platform"] not in PLATFORMS:
        errs.append(f"{slug}: bad careers_platform {s['careers_platform']!r}")
    if "careers_url" in s and not str(s["careers_url"]).startswith("http"):
        errs.append(f"{slug}: careers_url must be http(s)")
    v = s.get("careers_search_url", None)
    if v is not None and not str(v).startswith("http"):
        errs.append(f"{slug}: careers_search_url must be http(s) or null")
    if "ats_config" in pt and not isinstance(pt["ats_config"], dict):
        errs.append(f"{slug}: ats_config must be an object")
    if "—" in json.dumps(pt, ensure_ascii=False):
        errs.append(f"{slug}: patch contains an em dash")
    return errs


def apply(h: dict, pt: dict) -> None:
    for k, v in pt.get("set", {}).items():
        h[k] = v
    if "ats_config" in pt:
        old = h.get("ats_config") or {}
        new = {k: old[k] for k in KEEP_CFG if k in old}
        new.update(pt["ats_config"])
        h["ats_config"] = new
    if pt.get("note"):
        cur = (h.get("careers_notes") or "").rstrip()
        if pt["note"] not in cur:
            h["careers_notes"] = (cur + " " + pt["note"]).strip()
    for u in pt.get("sources", []):
        if u not in h.setdefault("sources", []):
            h["sources"].append(u)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    patch = load(Path(argv[0]))
    dry = "--dry-run" in argv
    ledger = load(LEDGER)
    by = {h["slug"]: h for h in ledger["hospitals"]}
    regions: dict[Path, dict] = {}
    where: dict[str, Path] = {}
    for f in sorted(glob.glob(str(REGIONS / "*.json"))):
        p = Path(f)
        regions[p] = load(p)
        for h in regions[p]["hospitals"]:
            where.setdefault(h["slug"], p)
    errs = []
    for slug, pt in patch.items():
        if slug not in by:
            errs.append(f"{slug}: not in {LEDGER.name}")
        if slug not in where:
            errs.append(f"{slug}: not in any region file")
        errs.extend(check_patch(slug, pt))
    if errs:
        print("refusing to write:\n  " + "\n  ".join(errs))
        return 1
    touched: set[Path] = set()
    for slug, pt in patch.items():
        apply(by[slug], pt)
        rp = where[slug]
        for h in regions[rp]["hospitals"]:
            if h["slug"] == slug:
                apply(h, pt)
        touched.add(rp)
        print(f"patched {slug} ({rp.name})")
    if dry:
        print(f"dry run: {len(patch)} slugs, would write {LEDGER.name} + {len(touched)} region files")
        return 0
    dump_atomic(LEDGER, ledger)
    for rp in sorted(touched):
        dump_atomic(rp, regions[rp])
    print(f"wrote {LEDGER.relative_to(ROOT)} and {len(touched)} region files for {len(patch)} slugs")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
