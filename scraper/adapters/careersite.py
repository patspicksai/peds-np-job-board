"""Jobvite / Talemetry "CareerSite" sites (window.csns.paths), e.g. jobs.unchealthcare.org, careersatmainehealth.org.

Verified live: both tenants sit behind Cloudflare. Plain requests (and curl_cffi Chrome impersonation) always get
the 403 "Just a moment..." challenge. In a real browser, the `.json` routes are not challenged
(cfprobe run 39364436106: headed Chrome and patchright pass every .json URL; bundled headless Chromium passes most):
  GET https://{host}/search/jobs.json?q={kw}&page={n}
  -> {"current_page":1,"per_page":25,"total_entries":N,"entries":[{"id","permalink","title",
       "location":{"street","locality","region_abbr","name"}}]}
  GET https://{host}/jobs/{id}-{permalink}.json   -> the posting page (HTML) with JSON-LD JobPosting (UNC) or the
                                                     description DOM only (MaineHealth: .job-details__main)
Posting URL (public, canonical): https://{host}/jobs/{id}-{permalink}
To keep browser time low, search pages and detail pages are fetched in batches with fetch() from inside one
page that already loaded a .json URL (browser.fetch_in_page), falling back to one render per URL.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote_plus, urlsplit

from bs4 import BeautifulSoup

from .. import browser, http, textutil
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
        street = loc.get("street")
        location = ", ".join(x for x in [street, city, st] if x) if street else \
            ", ".join(x for x in [name, ", ".join(y for y in [city, st] if y)] if x)
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


_META_RES = {
    "external_id": re.compile(r"(?:Req\s*#|Job\s*ID|Requisition\s*(?:#|ID|Number))\s*:\s*([A-Za-z0-9-]+)", re.I),
    "posted_at": re.compile(r"Date\s+Posted\s*:\s*([A-Za-z]{3,9}\.?\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})", re.I),
    "employment_type": re.compile(r"(?:Schedule|Work\s+Type|Status)\s*:\s*(Full[\s-]?Time|Part[\s-]?Time|Per\s+Diem|PRN|Casual|Temporary)", re.I),
}


def parse_careersite_detail(html: str, url: str) -> dict:
    """JSON-LD first; otherwise the description block of .job-details__main, plus header meta fields."""
    out = parse_detail_html(html, url)
    soup = BeautifulSoup(html or "", "lxml")
    main = soup.select_one("#job-details .job-details__main") or soup.select_one(".job-details__main")
    if main is not None:
        for t in main(["script", "style", "noscript", "form", "button"]):
            t.decompose()
        header_text = ""
        first_row = main.select_one("div.row")
        if first_row is not None:
            header_text = first_row.get_text("\n", strip=True)
        blocks = [b for b in main.select("div.padded-v-medium, div.page-section-medium, [itemprop=description], "
                                         ".job-description, .jobs-description") if b is not first_row]
        best = max(blocks, key=lambda b: len(b.get_text(" ", strip=True)), default=None)
        if best is not None:
            text = textutil.html_to_text(str(best))
            text = re.sub(r"^(?:Job\s+)?Description\s*\n+(?:Description\s*\n+)?", "", text).strip()
            text = textutil.trim_boilerplate_tail(text)
            if len(text) > len(out.get("description") or "") * 1.3 or not textutil.jsonld_jobposting(html):
                out["description"] = text
        for key, rx in _META_RES.items():
            if out.get(key):
                continue
            m = rx.search(header_text) or rx.search(main.get_text("\n", strip=True)[:3000])
            if m:
                v = m.group(1)
                out[key] = (textutil.parse_date(v) if key == "posted_at" else
                            textutil.normalize_employment(v) if key == "employment_type" else v)
    out.pop("closed", None) if out.get("description") else None
    return {k: v for k, v in out.items() if v}


def json_variant(url: str) -> str:
    return url.rstrip("/") + ".json"


class CareerSiteAdapter(Adapter):
    platform = "careersite"
    PER = 25
    MAX_PAGES = 6

    def __init__(self, hospital, deadline=None):
        super().__init__(hospital, deadline)
        self._cache: dict[str, str] = {}
        self._browser = bool(self.cfg.get("needs_browser", False))

    # ------------------------------------------------------------------ fetching

    def _base(self) -> str:
        host = self.cfg.get("host") or urlsplit(self.search_url).netloc
        return f"https://{host}"

    def _origin(self) -> str:
        return f"{self._base()}/search/jobs.json?q=nurse"

    def _requests_json(self, url: str):
        r = self.get(url, headers={"Accept": "application/json, text/javascript, */*; q=0.01",
                                   "X-Requested-With": "XMLHttpRequest"})
        self.method = "requests"
        return _json_from_page(r.text)

    def _browser_batch(self, urls: list[str]) -> dict[str, tuple[int, str]]:
        """{url: (status, text)} via in-page fetch; URLs that fail are retried with a full render."""
        out: dict[str, tuple[int, str]] = {}
        if not urls:
            return out
        try:
            for i in range(0, len(urls), 25):
                if self.out_of_time():
                    break
                for res in browser.fetch_in_page(self._origin(), urls[i:i + 25]):
                    out[res["url"]] = (int(res.get("status") or 0), res.get("text") or "")
            self.method = "playwright-fetch"
        except Exception as e:  # noqa: BLE001
            self.note(f"in-page fetch failed ({type(e).__name__}: {str(e)[:160]}); rendering one by one")
        for u in urls:
            st, txt = out.get(u, (0, ""))
            if st == 200 and "just a moment" not in txt[:3000].lower():
                continue
            if self.out_of_time():
                break
            try:
                res = browser.render(u, settle_ms=500)
                out[u] = (int(res.get("status") or 0), res.get("html") or "")
                self.method = self.method or "playwright"
            except Exception as e:  # noqa: BLE001
                out[u] = (0, f"{type(e).__name__}: {e}")
        return out

    def _search_urls(self, kw: str, pages: range) -> list[str]:
        base = self._base()
        return [f"{base}/search/jobs.json?q={quote_plus(kw)}" + (f"&page={p}" if p > 1 else "") for p in pages]

    # ------------------------------------------------------------------ interface

    def search(self, keywords):
        base = self._base()
        out: dict[str, Listing] = {}
        if not self._browser:
            try:
                data = self._requests_json(self._search_urls(keywords[0], range(1, 2))[0])
                return self._search_requests(keywords, data)
            except http.FetchError as e:
                if not http.is_bot_block(e):
                    raise
                self._browser = True
                self.note(f"requests blocked (HTTP {e.status}, Cloudflare); using the browser on .json routes")
            except ValueError as e:
                self.note(f"requests: jobs.json not JSON ({e}); using the browser")
                self._browser = True
        first = {kw: self._search_urls(kw, range(1, 2))[0] for kw in keywords}
        got = self._browser_batch(list(first.values()))
        errors, more = 0, []
        for kw, u in first.items():
            st, txt = got.get(u, (0, ""))
            try:
                if st != 200:
                    raise ValueError(f"HTTP {st}")
                rows, total = parse_careersite_json(_json_from_page(txt), base)
            except ValueError as e:
                errors += 1
                self.note(f"jobs.json '{kw}': {e} {txt[:80]!r}")
                continue
            for li in rows:
                out.setdefault(li.norm, li)
            pages = min(self.MAX_PAGES, -(-min(int(total or 0), self.PER * self.MAX_PAGES) // self.PER))
            more.extend(self._search_urls(kw, range(2, pages + 1)))
        if errors == len(keywords):
            # last resort: the HTML search page
            rows = self._html_search(keywords)
            if not rows:
                raise AdapterError("careersite: every jobs.json search failed in the browser: " + "; ".join(self.notes[-3:]))
            return rows
        for u, (st, txt) in self._browser_batch(more).items():
            try:
                rows, _ = parse_careersite_json(_json_from_page(txt), base)
            except ValueError:
                self.note(f"page fetch failed HTTP {st}: {u}")
                continue
            for li in rows:
                out.setdefault(li.norm, li)
        return list(out.values())

    def _search_requests(self, keywords, first_data) -> list[Listing]:
        base = self._base()
        out: dict[str, Listing] = {}
        for i, kw in enumerate(keywords):
            for page in range(1, self.MAX_PAGES + 1):
                if self.out_of_time():
                    break
                try:
                    data = first_data if (i == 0 and page == 1) else self._requests_json(self._search_urls(kw, range(page, page + 1))[0])
                    rows, total = parse_careersite_json(data, base)
                except (http.FetchError, ValueError) as e:
                    self.note(f"jobs.json '{kw}' p{page}: {e}")
                    break
                for li in rows:
                    out.setdefault(li.norm, li)
                if not rows or total is None or page * self.PER >= min(int(total), self.PER * self.MAX_PAGES):
                    break
        return list(out.values())

    def _html_search(self, keywords) -> list[Listing]:
        base = self._base()
        out: dict[str, Listing] = {}
        for kw in keywords[:2]:
            try:
                res = browser.render(f"{base}/search/jobs?q={quote_plus(kw)}", wait_selector=".jobs-section__item")
            except Exception as e:  # noqa: BLE001
                self.note(f"html search '{kw}': {type(e).__name__}: {str(e)[:120]}")
                continue
            if res.get("status") and res["status"] >= 400:
                self.note(f"html search '{kw}': HTTP {res['status']} {res.get('title', '')[:40]}")
                continue
            rows, _ = parse_careersite(res["html"], base)
            self.method = "playwright-html"
            for li in rows:
                out.setdefault(li.norm, li)
        return list(out.values())

    def prefetch(self, listings: list[Listing]) -> None:
        """Fetch many posting pages in one browser session (called by the pipeline before details())."""
        if not self._browser or not listings:
            return
        urls = [json_variant(li.url) for li in listings if json_variant(li.url) not in self._cache]
        for u, (st, txt) in self._browser_batch(urls).items():
            if st == 200 and txt:
                self._cache[u] = txt
            elif st in (404, 410):
                self._cache[u] = "__CLOSED__"

    def details(self, listing):
        if not self._browser:
            try:
                return self._enrich(parse_careersite_detail(self.get(listing.url).text, listing.url), listing)
            except http.FetchError as e:
                if e.status in (404, 410):
                    return {"closed": True}
                if not http.is_bot_block(e):
                    raise
                self._browser = True
        ju = json_variant(listing.url)
        html = self._cache.get(ju)
        if html is None:
            self.prefetch([listing])
            html = self._cache.get(ju)
        if html == "__CLOSED__":
            return {"closed": True}
        if not html:
            raise http.FetchError(listing.url, 403, "browser could not load the posting (Cloudflare)")
        return self._enrich(parse_careersite_detail(html, listing.url), listing)

    @staticmethod
    def _enrich(out: dict, listing: Listing) -> dict:
        ex = listing.extra
        if ex.get("city") and not out.get("city"):
            out["city"] = ex["city"]
        if ex.get("state") and not out.get("state"):
            out["state"] = ex["state"]
        if ex.get("facility") and not out.get("department"):
            out["department"] = ex["facility"]
        out.pop("hiring_org", None)
        return out
