#!/usr/bin/env python3
"""Apply an .enriched.json file (from scripts/enrich_hospitals.py run on Actions) onto a region file.

Only fills fields that are null / empty / placeholder in the region file; researcher-written facts win.
Keeps a trimmed `_enrich` object (wikipedia title, url, extract) for the description writers and the page.

Usage: python3 -I scripts/apply_enrichment.py data/regions/x.json logs/enrich/<run>/x.enriched.json
"""
import json
import re
import sys
from pathlib import Path

PLACEHOLDER_RE = re.compile(r"google\.[a-z.]+/maps|maps\.app\.goo\.gl|goo\.gl/maps", re.I)
PLATFORMS_OK = {"workday", "icims", "taleo", "successfactors", "phenom", "jobvite", "ultipro", "greenhouse",
                "lever", "smartrecruiters", "oracle_cloud", "eightfold", "pageup", "cornerstone", "adp",
                "avature", "dayforce", "paycom", "custom", "unknown"}


def is_placeholder(u):
    return not u or bool(PLACEHOLDER_RE.search(u))


def apply(h: dict, e: dict) -> list[str]:
    changed = []
    w = e.get("wikipedia") or {}
    nom = e.get("nominatim")
    if not (isinstance(h.get("lat"), (int, float)) and isinstance(h.get("lng"), (int, float))):
        if w.get("coords"):
            h["lat"], h["lng"] = w["coords"]["lat"], w["coords"]["lng"]
            h["coord_source"] = "Wikipedia article coordinates (API)"
            changed.append("coords:wiki")
        elif nom:
            h["lat"], h["lng"] = nom["lat"], nom["lng"]
            h["coord_source"] = f"OpenStreetMap Nominatim ({nom.get('osm_type')}) for '{nom.get('query')}'"
            changed.append("coords:nominatim")
    if not h.get("wikipedia_title") and w.get("title"):
        h["wikipedia_title"] = w["title"]
        changed.append("wikipedia_title")
    if not h.get("photos") and w.get("image", {}).get("url"):
        img = w["image"]
        h["photos"] = [{
            "url": img["url"],
            "caption": w.get("title") or h.get("name"),
            "credit": img.get("credit") or "Wikimedia Commons",
            "license": img.get("license") or "see source page",
            "source_page": img.get("source_page") or w.get("url"),
        }]
        changed.append("photo:wiki")
    if not h.get("photos") and e.get("website_og_image", {}).get("url"):
        og = e["website_og_image"]
        h["photos"] = [{
            "url": og["url"],
            "caption": h.get("name"),
            "credit": (h.get("health_system") or h.get("name")) + " (hospital website)",
            "license": "Hospital website image, all rights reserved; linked from the hospital's own site",
            "source_page": og.get("page") or h.get("website"),
        }]
        changed.append("photo:website")
    if is_placeholder(h.get("website")) and e.get("website_from_wikidata"):
        h["website"] = e["website_from_wikidata"]
        changed.append("website:wikidata")
    cp = e.get("careers_probe") or {}
    cd = e.get("careers_discovered") or {}
    if cd.get("status") == 200 and (cd.get("platform_guess") or cd.get("ats_link_seen")) and (not cp or not cp.get("platform_guess")):
        # a real careers page was discovered from the main site; adopt it
        if is_placeholder(h.get("careers_url")) or h.get("careers_url") == h.get("website") or not cp.get("platform_guess"):
            h["careers_url"] = cd.get("final_url") or h.get("careers_url")
            changed.append("careers_url:discovered")
        cp = {**cd, "status": cd.get("status")}
    if cp:
        guess = cp.get("platform_guess")
        if h.get("careers_platform") in (None, "unknown", "custom") and guess in PLATFORMS_OK and guess != "custom":
            h["careers_platform"] = guess
            changed.append(f"platform:{guess}")
        cfg = h.get("ats_config") or {}
        if not cfg.get("host") and cp.get("final_url"):
            from urllib.parse import urlsplit
            host = urlsplit(cp["final_url"]).netloc
            if host:
                cfg["host"] = host
                h["ats_config"] = cfg
                changed.append("ats_host")
        note = f"[enrich {e.get('enriched_at','')[:10]}] careers_url -> HTTP {cp.get('status')} at {cp.get('final_url')}"
        if cp.get("ats_link_seen"):
            note += f"; ATS link seen: {cp['ats_link_seen']}"
        sp = e.get("careers_search_probe") or {}
        if sp:
            note += f"; search_url -> HTTP {sp.get('status')}, mentions NP: {sp.get('mentions')}, job links: {sp.get('job_link_count')}"
        h["careers_notes"] = ((h.get("careers_notes") or "") + " " + note).strip()
    if is_placeholder(h.get("careers_url")) and h.get("website") and not is_placeholder(h.get("website")):
        changed.append("careers_url:still_placeholder")
    if w:
        h["_enrich"] = {"wikipedia": {k: w.get(k) for k in ("title", "url", "extract") if w.get(k)}}
    elif not isinstance(h.get("_enrich"), dict):
        h["_enrich"] = {}
    if w.get("extract") and len(h.get("description") or "") < 120:
        changed.append("description:needs_writing")
    return changed


def main(argv):
    region, enriched = Path(argv[1]), Path(argv[2])
    doc = json.loads(region.read_text())
    en = {h["slug"]: h.get("_enrich", {}) for h in json.loads(enriched.read_text()).get("hospitals", [])}
    n = 0
    for h in doc.get("hospitals", []):
        e = en.get(h.get("slug"))
        if not e:
            continue
        ch = apply(h, e)
        if ch:
            n += 1
            print(f"{h['slug']}: {', '.join(ch)}")
    region.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    print(f"updated {n} records in {region}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
