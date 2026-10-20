#!/usr/bin/env python3
"""Enrich hospital records from the open web (runs on GitHub Actions, which has internet access).

For every hospital in the input file(s) it gathers, without overwriting facts the researchers wrote:
- Wikipedia: resolved article title and URL, intro extract (plain text), coordinates, lead image
  (hotlinkable upload.wikimedia.org URL) with credit and license from Commons extmetadata.
- Wikidata: official website (P856) when the record's website is missing or a maps placeholder.
- Nominatim (OpenStreetMap): coordinates when Wikipedia has none.
- Careers site: final URL after redirects, HTTP status, platform guess from the final host, path and
  page signatures, and whether the careers_search_url response mentions "nurse practitioner".

Writes <input>.enriched.json next to each input (or into --out-dir). The `_enrich` object on each
record carries everything gathered; top-level fields are only FILLED where they are null, empty, or an
obvious placeholder (a Google Maps link in website/careers_url). Apply with scripts/apply_enrichment.py.

Usage: python3 scripts/enrich_hospitals.py data/regions/x.json [more...] [--out-dir DIR] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests

UA = "peds-np-job-board/0.1 (research enrichment; contact via GitHub patspicksai/peds-np-job-board)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/141.0.0.0 Safari/537.36")
WIKI_API = "https://en.wikipedia.org/w/api.php"
S = requests.Session()
S.headers.update({"User-Agent": UA})

PLACEHOLDER_RE = re.compile(r"google\.[a-z.]+/maps|maps\.app\.goo\.gl|goo\.gl/maps|^$", re.I)

SIGNATURES = [
    ("myworkdayjobs.com", "workday"), ("myworkdaysite.com", "workday"), ("icims.com", "icims"),
    ("taleo.net", "taleo"), ("oraclecloud.com", "oracle_cloud"), ("successfactors", "successfactors"),
    ("jobs.sap.com", "successfactors"), ("phenompeople", "phenom"), ("phenom.com", "phenom"),
    ("ultipro.com", "ultipro"), ("ukg.net", "ultipro"), ("eightfold.ai", "eightfold"),
    ("pageuppeople.com", "pageup"), ("csod.com", "cornerstone"), ("jobvite.com", "jobvite"),
    ("symphonytalent", "symphony"), ("ttcportals", "talentbrew"), ("talentbrew", "talentbrew"),
    ("jibeapply", "jibe"), ("jibe.com", "jibe"), ("avature.net", "avature"), ("dayforcehcm.com", "dayforce"),
    ("paycomonline", "paycom"), ("greenhouse.io", "greenhouse"), ("lever.co", "lever"),
    ("smartrecruiters.com", "smartrecruiters"), ("brassring.com", "brassring"), ("kenexa", "brassring"),
    ("adp.com", "adp"), ("healthcaresource", "healthcaresource"), ("hrsmart", "custom"),
    ("infor.com", "infor"), ("inforcloudsuite", "infor"), ("talemetry", "careersite"),
]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def is_placeholder(url: str | None) -> bool:
    return not url or bool(PLACEHOLDER_RE.search(url))


def wiki_query(params: dict) -> dict:
    p = {"format": "json", "formatversion": "2", "redirects": "1"}
    p.update(params)
    for attempt in range(3):
        try:
            r = S.get(WIKI_API, params=p, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            log("   wiki error", e)
            time.sleep(2)
    return {}


def wiki_resolve_title(h: dict) -> str | None:
    cands = []
    if h.get("wikipedia_title"):
        cands.append(h["wikipedia_title"])
    cands.append(h.get("name", ""))
    if h.get("parent_hospital"):
        cands.append(h["parent_hospital"])
    for c in cands:
        if not c:
            continue
        j = wiki_query({"action": "query", "titles": c, "prop": "info"})
        pages = j.get("query", {}).get("pages", [])
        if pages and not pages[0].get("missing") and pages[0].get("ns") == 0:
            return pages[0]["title"]
    # search fallback
    q = f'{h.get("name","")} hospital {h.get("city","")} {h.get("state","")}'
    j = wiki_query({"action": "query", "list": "search", "srsearch": q, "srlimit": 5})
    for hit in j.get("query", {}).get("search", []):
        t = hit["title"]
        tl = t.lower()
        if any(w in tl for w in ("hospital", "medical center", "children", "health", "infirmary", "clinic")):
            return t
    return None


def wiki_fetch(title: str) -> dict:
    j = wiki_query({
        "action": "query", "titles": title,
        "prop": "extracts|coordinates|pageimages|pageprops|info",
        "exintro": "1", "explaintext": "1", "piprop": "original|thumbnail", "pithumbsize": "1200",
        "inprop": "url",
    })
    pages = j.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return {}
    p = pages[0]
    out = {
        "title": p.get("title"), "url": p.get("fullurl"), "extract": (p.get("extract") or "").strip(),
        "wikibase_item": (p.get("pageprops") or {}).get("wikibase_item"),
        "page_image_file": (p.get("pageprops") or {}).get("page_image_free"),
    }
    if p.get("coordinates"):
        c = p["coordinates"][0]
        out["coords"] = {"lat": round(c["lat"], 5), "lng": round(c["lon"], 5)}
    if p.get("original"):
        out["image"] = {"url": p["original"]["source"], "width": p["original"].get("width")}
        if p.get("thumbnail"):
            out["image"]["thumb"] = p["thumbnail"]["source"]
    return out


def wiki_image_meta(file_title: str | None, image_url: str | None) -> dict:
    if not file_title and image_url:
        file_title = image_url.rsplit("/", 1)[-1]
    if not file_title:
        return {}
    ft = file_title if file_title.lower().startswith("file:") else "File:" + file_title
    for api in (WIKI_API, "https://commons.wikimedia.org/w/api.php"):
        try:
            r = S.get(api, params={"action": "query", "titles": ft, "prop": "imageinfo", "iiprop": "extmetadata|url",
                                   "format": "json", "formatversion": "2"}, timeout=30)
            pages = r.json().get("query", {}).get("pages", [])
            if pages and pages[0].get("imageinfo"):
                ii = pages[0]["imageinfo"][0]
                em = ii.get("extmetadata", {})
                strip = lambda s: re.sub(r"<[^>]+>", "", s or "").strip()  # noqa: E731
                return {
                    "credit": strip((em.get("Artist") or {}).get("value")) or strip((em.get("Credit") or {}).get("value")),
                    "license": strip((em.get("LicenseShortName") or {}).get("value")),
                    "license_url": strip((em.get("LicenseUrl") or {}).get("value")),
                    "description": strip((em.get("ImageDescription") or {}).get("value"))[:200],
                    "source_page": ii.get("descriptionurl"),
                }
        except Exception as e:  # noqa: BLE001
            log("   imageinfo error", e)
    return {}


def wikidata_website(qid: str | None) -> str | None:
    if not qid:
        return None
    try:
        r = S.get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json", timeout=30)
        ent = r.json()["entities"][qid]
        claims = ent.get("claims", {})
        for c in claims.get("P856", []):
            v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
            if v:
                return v
    except Exception as e:  # noqa: BLE001
        log("   wikidata error", e)
    return None


_last_nom = [0.0]


def nominatim(h: dict) -> dict | None:
    q = f'{h.get("name","")}, {h.get("city","")}, {h.get("state","")}'
    for query in (q, f'{h.get("address","") or ""}, {h.get("city","")}, {h.get("state","")}'):
        if not query.strip(", "):
            continue
        wait = 1.1 - (time.time() - _last_nom[0])
        if wait > 0:
            time.sleep(wait)
        _last_nom[0] = time.time()
        try:
            r = S.get("https://nominatim.openstreetmap.org/search",
                      params={"q": query, "format": "json", "limit": 3, "countrycodes": "us"}, timeout=30)
            res = r.json()
        except Exception as e:  # noqa: BLE001
            log("   nominatim error", e)
            continue
        if not res:
            continue
        best = next((x for x in res if x.get("type") == "hospital" or x.get("class") == "amenity"), res[0])
        return {"lat": round(float(best["lat"]), 5), "lng": round(float(best["lon"]), 5),
                "display_name": best.get("display_name"), "osm_type": best.get("type"), "query": query}
    return None


def probe_url(url: str, want_text: str | None = None) -> dict:
    out = {"url": url}
    try:
        r = requests.get(url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9",
                                       "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
                         timeout=30, allow_redirects=True)
        out["status"] = r.status_code
        out["final_url"] = r.url
        html = r.text[:400000] if "text" in r.headers.get("Content-Type", "") or r.text[:1].strip().startswith("<") else ""
        low = (r.url + "\n" + html).lower()
        sigs = sorted({name for needle, name in SIGNATURES if needle in low})
        out["signals"] = sigs
        host = urlsplit(r.url).netloc.lower()
        out["platform_guess"] = None
        for needle, name in SIGNATURES:
            if needle in host:
                out["platform_guess"] = name
                break
        if not out["platform_guess"] and sigs:
            out["platform_guess"] = sigs[0]
        if "<title" in low:
            m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
            if m:
                out["title"] = re.sub(r"\s+", " ", m.group(1)).strip()[:160]
        if want_text:
            out["mentions"] = want_text.lower() in low
            out["job_link_count"] = len(re.findall(r"href=\"[^\"]*(?:/job/|/jobs/|jobid=|/req|JobDetail|/careers/[^\"]*\d)", html, re.I))
        # a few common hidden-ATS tells
        m = re.search(r"https?://[a-z0-9.-]+\.(?:myworkdayjobs\.com|icims\.com|taleo\.net|oraclecloud\.com|ultipro\.com|eightfold\.ai)[^\s\"'<>]*", html, re.I)
        if m:
            out["ats_link_seen"] = m.group(0)[:200]
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"[:200]
    return out


CAREERS_LINK_RE = re.compile(r'href=["\']([^"\']*(?:career|careers|jobs|employment|join-our-team|work-with-us|joinus|opportunit)[^"\']*)["\']', re.I)


def discover_careers_link(page_url: str) -> str | None:
    """Fetch a page and return the most careers-looking link on it (absolute URL), else None."""
    try:
        r = requests.get(page_url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"},
                         timeout=30, allow_redirects=True)
        if r.status_code != 200:
            return None
        from urllib.parse import urljoin
        cands = []
        for m in CAREERS_LINK_RE.finditer(r.text[:600000]):
            href = m.group(1).strip()
            if href.startswith(("mailto:", "javascript:", "#")):
                continue
            absu = urljoin(r.url, href)
            score = 0
            lo = absu.lower()
            for needle, name in SIGNATURES:
                if needle in lo:
                    score += 10
            if "career" in lo or "jobs" in lo:
                score += 3
            if lo.endswith((".pdf", ".jpg", ".png")):
                continue
            cands.append((score, absu))
        if not cands:
            return None
        cands.sort(reverse=True)
        return cands[0][1]
    except Exception as e:  # noqa: BLE001
        log("   discover error", e)
        return None


OG_RE = re.compile(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]*content=["\']([^"\']+)["\']', re.I)
OG_RE2 = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\'](?:og:image|twitter:image)["\']', re.I)


def website_og_image(url: str) -> dict | None:
    """Return {url, page} for the site's og:image / twitter:image, if it looks like a real photo."""
    try:
        r = requests.get(url, headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"},
                         timeout=30, allow_redirects=True)
        if r.status_code != 200:
            return None
        html = r.text[:400000]
        m = OG_RE.search(html) or OG_RE2.search(html)
        if not m:
            return None
        from urllib.parse import urljoin
        img = urljoin(r.url, m.group(1).strip())
        lo = img.lower()
        if any(x in lo for x in ("logo", "icon", "favicon", ".svg", "placeholder", "default-share", "og-default")):
            return None
        # confirm it is an image
        h = requests.head(img, headers={"User-Agent": BROWSER_UA}, timeout=20, allow_redirects=True)
        ct = h.headers.get("Content-Type", "")
        if h.status_code == 200 and ct.startswith("image/") and int(h.headers.get("Content-Length", "100000") or 100000) > 15000:
            return {"url": img, "page": r.url, "content_type": ct}
    except Exception as e:  # noqa: BLE001
        log("   og:image error", e)
    return None


def enrich(h: dict) -> dict:
    e = h.setdefault("_enrich", {})
    e["enriched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # Wikipedia
    title = wiki_resolve_title(h)
    if title:
        w = wiki_fetch(title)
        if w:
            e["wikipedia"] = w
            if w.get("image"):
                w["image"].update(wiki_image_meta(w.get("page_image_file"), w["image"]["url"]))
            if is_placeholder(h.get("website")):
                site = wikidata_website(w.get("wikibase_item"))
                if site:
                    e["website_from_wikidata"] = site
    # photo fallback: the hospital site's share image
    if not h.get("photos") and not e.get("wikipedia", {}).get("image"):
        site = h.get("website")
        if site and not is_placeholder(site):
            og = website_og_image(site)
            if og:
                e["website_og_image"] = og
    # geocode
    if not (isinstance(h.get("lat"), (int, float)) and isinstance(h.get("lng"), (int, float))) and not e.get("wikipedia", {}).get("coords"):
        n = nominatim(h)
        if n:
            e["nominatim"] = n
    # careers
    cu = h.get("careers_url")
    if cu and not is_placeholder(cu):
        e["careers_probe"] = probe_url(cu)
        # If the "careers" URL is really the main site (or landed on a marketing page with no ATS
        # signals), look for a careers / jobs link on that page and follow it.
        cp = e["careers_probe"]
        if cp.get("status") == 200 and not cp.get("platform_guess") and not cp.get("ats_link_seen"):
            found = discover_careers_link(cp.get("final_url") or cu)
            if found:
                e["careers_discovered"] = probe_url(found)
                e["careers_discovered"]["from"] = cp.get("final_url") or cu
    elif h.get("website") and not is_placeholder(h.get("website")):
        found = discover_careers_link(h["website"])
        if found:
            e["careers_discovered"] = probe_url(found)
            e["careers_discovered"]["from"] = h["website"]
    su = h.get("careers_search_url")
    if su and not is_placeholder(su):
        e["careers_search_probe"] = probe_url(su, want_text="nurse practitioner")
    site = h.get("website")
    if site and not is_placeholder(site) and not cu:
        e["website_probe"] = probe_url(site)
    return h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--photos-only", action="store_true", help="only look for a website share image for records without photos")
    ap.add_argument("--regeocode", action="store_true", help="re-geocode records whose coord_source cites Google Places using Wikipedia then OpenStreetMap; keeps the OSM point only if within 2.5 km of the current one")
    a = ap.parse_args()
    for f in a.files:
        src = Path(f)
        doc = json.loads(src.read_text())
        hs = doc.get("hospitals", [])
        if a.limit:
            hs = hs[: a.limit]
        log(f"== {src.name}: {len(hs)} hospitals")
        for i, h in enumerate(hs):
            t0 = time.time()
            try:
                if a.regeocode:
                    h.setdefault("_enrich", {})
                    if "google" in (h.get("coord_source") or "").lower() and isinstance(h.get("lat"), (int, float)):
                        import math
                        def _km(a1, b1):
                            p1, p2 = math.radians(a1[0]), math.radians(b1[0]); dl = math.radians(b1[1] - a1[1]); dp = p2 - p1
                            x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
                            return 2 * 6371 * math.asin(math.sqrt(x))
                        cand = None
                        title = wiki_resolve_title(h)
                        w = wiki_fetch(title) if title else {}
                        if w.get("coords") and _km((h["lat"], h["lng"]), (w["coords"]["lat"], w["coords"]["lng"])) < 2.5:
                            cand = (w["coords"]["lat"], w["coords"]["lng"], f"Wikipedia article coordinates ({w.get('title')})")
                        if not cand:
                            n = nominatim(h)
                            if n and _km((h["lat"], h["lng"]), (n["lat"], n["lng"])) < 2.5:
                                cand = (n["lat"], n["lng"], f"OpenStreetMap Nominatim ({n.get('osm_type')}) for '{n.get('query')}'")
                        if cand:
                            h["_enrich"]["regeocode"] = {"lat": cand[0], "lng": cand[1], "coord_source": cand[2]}
                elif a.photos_only:
                    h.setdefault("_enrich", {})
                    if not h.get("photos") and h.get("website") and not is_placeholder(h.get("website")):
                        og = website_og_image(h["website"])
                        if og:
                            h["_enrich"]["website_og_image"] = og
                else:
                    enrich(h)
            except Exception as ex:  # noqa: BLE001
                h.setdefault("_enrich", {})["error"] = f"{type(ex).__name__}: {ex}"[:300]
            w = h.get("_enrich", {}).get("wikipedia", {})
            log(f"   [{i+1}/{len(hs)}] {h.get('slug')}: wiki={'yes' if w else 'no'} coords={'wiki' if w.get('coords') else ('nom' if h.get('_enrich',{}).get('nominatim') else '-')} "
                f"img={'yes' if w.get('image') else 'no'} careers={h.get('_enrich',{}).get('careers_probe',{}).get('status','-')} "
                f"plat={h.get('_enrich',{}).get('careers_probe',{}).get('platform_guess')} ({time.time()-t0:.1f}s)")
        doc["hospitals"] = hs
        out_dir = Path(a.out_dir) if a.out_dir else src.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / (src.stem + ".enriched.json")
        out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        log(f"   wrote {out}")


if __name__ == "__main__":
    main()
