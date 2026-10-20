"""Jobvite / Talemetry "CareerSite" sites (window.csns.paths), e.g. jobs.unchealthcare.org, careersatmainehealth.org.

Verified live: both tenants sit behind Cloudflare (requests -> 403 "Just a moment..."). In headless Chromium the
HTML search page stays on the challenge, but the JSON endpoint loads fine:
  GET https://{host}/search/jobs.json?q={kw}&page={n}
  -> {"current_page":1,"per_page":25,"total_entries":N,"entries":[{"id","permalink","title",
       "location":{"street","locality","region_abbr","name"}}]}
Posting URL: https://{host}/jobs/{id}-{permalink}
Detail: posting page has JSON-LD JobPosting (rendered via Playwright when requests is blocked).
HTML search fallback (/search/jobs?q=) is parsed too, for tenants without the JSON route.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote_plus, urlsplit

from bs4 import BeautifulSoup

from .. import browser, http
from ..models import Listing
from .base import Adapter, AdapterError, parse_detail_html


def _json_from_page(html_or_text: str):
    t = (html_or_text or "").strip()
    if t.startswith("{"):
        return json.loads(t)
    m = re.search(r"<pre[^>]*>(.*?)</pre>", t, re.S)
    if m:
        import html as htmllib
        return json.loads(htmllib.unescape(m.group(1)))
    raise ValueError("no JSON body")


def parse_careersite_json(data: dict, base: str) -> tuple[list[Listing], int | None]:
    out = []
    for e in data.get("entries") or []:
        jid, perm = e.get("id"), e.get("permalink")
        if not jid:
            continue
        loc = e.get("location") or {}
        city, st, name = loc.get("locality"), loc.get("region_abbr"), loc.get("name")
        location = ", ".join(x for x in [name, ", ".join(y for y in [city, st] if y)] if x)
        out.append(Listing(title=(e.get("title") or "").strip(),
                           url=f"{base}/jobs/{jid}-{perm}" if perm else f"{base}/jobs/{jid}",
                           location_text=location, external_id=str(jid),
                           extra={"city": city, "state": st, "facility": name}))
    return out, data.get("total_entries")


def parse_careersite(html: str, base: str) -> tuple[list[Listing], int | None]:
    soup = BeautifulSoup(html or "", "lxml")
    total = None
    m = re.search(r"Showing\s+\d+\s*-\s*\d+\s+of\s+([\d,]+)", soup.get_text(" ", strip=True))
    if m:
        total = int(m.group(1).replace(",", ""))
    out: list[Listing] = []
    for item in soup.select(".jobs-section__item"):
        a = item.select_one("h5 a[href], h4 a[href], a[href*='/jobs/']")
        if not a:
            continue
        href = a["href"]
        cols = [c.get_text(" ", strip=True) for c in item.select(".columns")]
        jid = None
        h6 = item.select_one("h6")
        if h6:
            mm = re.search(r"Job ID:\s*(\S+)", h6.get_text(" ", strip=True))
            jid = mm.group(1) if mm else None
        loc, fac = "", ""
        for c in cols[1:]:
            c2 = re.sub(r"^(Status|Location|Facility/Division):\s*", "", c).strip()
            if re.search(r",\s*[A-Z]{2}$", c2):
                loc = re.sub(r"\s+", " ", c2)
            elif "Facility/Division" in c:
                fac = c2
        out.append(Listing(title=a.get_text(" ", strip=True),
                           url=href if href.startswith("http") else base.rstrip("/") + href,
                           location_text=", ".join(x for x in [fac, loc] if x), external_id=jid,
                           extra={"facility": fac}))
    return out, total


class CareerSiteAdapter(Adapter):
    platform = "careersite"
    PER = 25

    def _fetch_json(self, url: str):
        if not self._browser:
            try:
                r = self.get(url, headers={"Accept": "application/json, text/javascript, */*; q=0.01",
                                           "X-Requested-With": "XMLHttpRequest"})
                self.method = "requests"
                return _json_from_page(r.text)
            except http.FetchError as e:
                if not http.is_bot_block(e):
                    raise
                self._browser = True
                self.note(f"requests blocked (HTTP {e.status}, Cloudflare); using Playwright")
        res = browser.render(url, settle_ms=800)
        self.method = "playwright"
        if res.get("status") and res["status"] >= 400:
            raise http.FetchError(url, res["status"], f"playwright: {res.get('title', '')[:60]}")
        return _json_from_page(res["html"])

    def search(self, keywords):
        host = self.cfg.get("host") or urlsplit(self.search_url).netloc
        base = f"https://{host}"
        self._browser = bool(self.cfg.get("needs_browser", False))
        out: dict[str, Listing] = {}
        errors = 0
        for kw in keywords:
            for page in range(1, 7):
                if self.out_of_time():
                    break
                url = f"{base}/search/jobs.json?q={quote_plus(kw)}" + (f"&page={page}" if page > 1 else "")
                try:
                    data = self._fetch_json(url)
                    rows, total = parse_careersite_json(data, base)
                except (http.FetchError, ValueError) as e:
                    self.note(f"jobs.json '{kw}': {e}")
                    try:
                        res = browser.render(f"{base}/search/jobs?q={quote_plus(kw)}", wait_selector=".jobs-section__item")
                        rows, total = parse_careersite(res["html"], base)
                        self.method = "playwright-html"
                        if not rows and res.get("status", 200) >= 400:
                            raise http.FetchError(url, res["status"], "html search also blocked")
                    except Exception as e2:  # noqa: BLE001
                        errors += 1
                        self.note(f"search '{kw}': {e2}")
                        break
                for li in rows:
                    out.setdefault(li.norm, li)
                if not rows or total is None or page * self.PER >= min(int(total), 150):
                    break
        if errors == len(keywords):
            raise AdapterError("; ".join(self.notes[-3:]))
        return list(out.values())

    def details(self, listing):
        if not self._browser:
            try:
                return self._enrich(parse_detail_html(self.get(listing.url).text, listing.url), listing)
            except http.FetchError as e:
                if e.status in (404, 410):
                    return {"closed": True}
                if not http.is_bot_block(e):
                    raise
                self._browser = True
        res = browser.render(listing.url, settle_ms=800)
        if res.get("status") and res["status"] >= 400:
            raise http.FetchError(listing.url, res["status"], "playwright")
        return self._enrich(parse_detail_html(res["html"], listing.url), listing)

    @staticmethod
    def _enrich(out: dict, listing: Listing) -> dict:
        ex = listing.extra
        if ex.get("city") and not out.get("city"):
            out["city"] = ex["city"]
        if ex.get("state") and not out.get("state"):
            out["state"] = ex["state"]
        if ex.get("facility") and not out.get("department"):
            out["department"] = ex["facility"]
        return out
