#!/usr/bin/env python3
"""Validate a hospitals JSON file (region slice or merged ledger) against spec/hospital_schema.md.

Usage: python3 -I scripts/validate.py data/regions/north_carolina.json [more files...]
Exit code 1 on any error. Warnings do not fail.
"""
import json
import math
import re
import sys
from pathlib import Path

REGIONS = {
    "Portland ME": True, "Maine (other)": False, "New Hampshire": False, "Vermont": False,
    "Boston metro": False, "Massachusetts (other)": False, "Rhode Island": False, "Connecticut": False,
    "NYC metro": False, "Long Island": False, "Hudson Valley": False, "Upstate NY": False,
    "Jersey Shore": True, "North Jersey": False, "South Jersey": False,
    "Philadelphia metro": False, "Pennsylvania (other)": False, "Delaware": False,
    "Baltimore metro": False, "DC / Northern Virginia": False, "Maryland (other)": False,
    "Richmond": False, "Hampton Roads": False, "Virginia (other)": False,
    "Triangle": True, "Charlotte": True, "Wilmington / Cape Fear": True, "Outer Banks / Coastal NC": True,
    "Triad": False, "North Carolina (other)": False,
    "Charleston SC": False, "Grand Strand": False, "South Carolina (other)": False,
}
HOSPITAL_TYPES = {"childrens_hospital", "childrens_hospital_within_hospital", "academic_medical_center",
                  "regional_hospital", "community_hospital", "critical_access", "specialty_pediatric"}
PLATFORMS = {"workday", "icims", "taleo", "successfactors", "phenom", "jobvite", "ultipro", "greenhouse",
             "lever", "smartrecruiters", "oracle_cloud", "eightfold", "pageup", "cornerstone", "adp",
             "avature", "dayforce", "paycom", "custom", "unknown"}
CONF = {"high", "medium", "low"}
STATES = {"ME", "NH", "VT", "MA", "RI", "CT", "NY", "NJ", "PA", "DE", "MD", "DC", "VA", "NC", "SC"}

REQUIRED = [
    "slug", "name", "parent_hospital", "health_system", "hospital_type", "city", "state", "address",
    "lat", "lng", "coord_source", "region", "hotspot", "website", "careers_url", "careers_platform",
    "careers_search_url", "careers_notes", "ats_config", "nearest_airport", "beds", "peds_beds",
    "peds_inpatient", "picu", "picu_beds", "nicu_level", "peds_ed", "peds_trauma_level", "magnet",
    "teaching", "affiliated_university", "peds_services", "description", "highlights", "photos",
    "wikipedia_title", "sources", "notes", "confidence", "active",
]


def haversine_m(a, b):
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = p2 - p1
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def check(path):
    errors, warnings = [], []
    try:
        doc = json.loads(Path(path).read_text())
    except Exception as e:  # noqa: BLE001
        return [f"{path}: cannot parse JSON: {e}"], []
    hs = doc.get("hospitals")
    if not isinstance(hs, list) or not hs:
        return [f"{path}: 'hospitals' must be a non-empty list"], []
    slugs = {}
    for i, h in enumerate(hs):
        tag = f"{path}[{i}] {h.get('slug') or h.get('name') or '?'}"
        for k in REQUIRED:
            if k not in h:
                errors.append(f"{tag}: missing key '{k}'")
        slug = h.get("slug")
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug or ""):
            errors.append(f"{tag}: bad slug {slug!r}")
        elif slug in slugs:
            errors.append(f"{tag}: duplicate slug (also at index {slugs[slug]})")
        else:
            slugs[slug] = i
        if h.get("hospital_type") not in HOSPITAL_TYPES:
            errors.append(f"{tag}: bad hospital_type {h.get('hospital_type')!r}")
        if h.get("careers_platform") not in PLATFORMS:
            errors.append(f"{tag}: bad careers_platform {h.get('careers_platform')!r}")
        if h.get("confidence") not in CONF:
            errors.append(f"{tag}: bad confidence {h.get('confidence')!r}")
        if h.get("state") not in STATES:
            errors.append(f"{tag}: bad state {h.get('state')!r}")
        region = h.get("region")
        if region not in REGIONS:
            errors.append(f"{tag}: region {region!r} not in controlled list")
        elif h.get("hotspot") != REGIONS[region]:
            errors.append(f"{tag}: hotspot must be {REGIONS[region]} for region {region!r}")
        lat, lng = h.get("lat"), h.get("lng")
        if not (isinstance(lat, (int, float)) and 24 <= lat <= 48):
            errors.append(f"{tag}: lat out of range: {lat!r}")
        if not (isinstance(lng, (int, float)) and -83 <= lng <= -66):
            errors.append(f"{tag}: lng out of range: {lng!r}")
        for k in ("website", "careers_url"):
            v = h.get(k)
            if not (isinstance(v, str) and v.startswith("http")):
                errors.append(f"{tag}: {k} must be an http(s) URL, got {v!r}")
        v = h.get("careers_search_url")
        if v is not None and not (isinstance(v, str) and v.startswith("http")):
            errors.append(f"{tag}: careers_search_url must be URL or null")
        for k in ("peds_inpatient", "picu", "peds_ed", "teaching", "active", "hotspot"):
            if not isinstance(h.get(k), bool):
                errors.append(f"{tag}: {k} must be boolean")
        if h.get("magnet") not in (True, False, None):
            errors.append(f"{tag}: magnet must be boolean or null")
        for k in ("beds", "peds_beds", "picu_beds", "nicu_level"):
            v = h.get(k)
            if v is not None and not isinstance(v, int):
                errors.append(f"{tag}: {k} must be integer or null, got {v!r}")
        nl = h.get("nicu_level")
        if nl is not None and nl not in (1, 2, 3, 4):
            errors.append(f"{tag}: nicu_level must be 1-4 or null")
        for k in ("peds_services", "highlights", "sources"):
            if not isinstance(h.get(k), list):
                errors.append(f"{tag}: {k} must be a list")
        if not isinstance(h.get("photos"), list):
            errors.append(f"{tag}: photos must be a list")
        else:
            for p in h["photos"]:
                if not isinstance(p, dict) or not str(p.get("url", "")).startswith("http"):
                    errors.append(f"{tag}: photo entries need an http url")
        if not isinstance(h.get("ats_config"), dict):
            errors.append(f"{tag}: ats_config must be an object")
        d = h.get("description") or ""
        if len(d) < 120:
            warnings.append(f"{tag}: description is short ({len(d)} chars)")
        if "—" in json.dumps(h):
            errors.append(f"{tag}: contains an em dash; replace it")
        if not h.get("sources"):
            warnings.append(f"{tag}: no sources listed")
        if not h.get("photos"):
            warnings.append(f"{tag}: no photos")
    # near-duplicate check
    pts = [(h.get("lat"), h.get("lng"), h.get("slug")) for h in hs if isinstance(h.get("lat"), (int, float))]
    for a in range(len(pts)):
        for b in range(a + 1, len(pts)):
            try:
                if haversine_m(pts[a][:2], pts[b][:2]) < 150:
                    warnings.append(f"{path}: {pts[a][2]} and {pts[b][2]} are within 150 m (same campus? ok if child/parent)")
            except Exception:  # noqa: BLE001
                pass
    return errors, warnings


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    total_err = 0
    for p in argv[1:]:
        errs, warns = check(p)
        n = 0
        try:
            n = len(json.loads(Path(p).read_text()).get("hospitals", []))
        except Exception:  # noqa: BLE001
            pass
        print(f"== {p}: {n} hospitals, {len(errs)} errors, {len(warns)} warnings")
        for e in errs:
            print("ERROR  ", e)
        for w in warns:
            print("warn   ", w)
        total_err += len(errs)
    return 1 if total_err else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
