"""Symphony Talent CWS (WordPress "cws" career sites backed by the m-cloud jobs API), e.g. jobs.bostonchildrens.org.

Verified live (Boston Children's, probe 39364438124): the search results page is plain HTML that loads results
with a JSONP call; the page source carries everything needed to make that call ourselves:
  CWS.jobs.set_api("https://jobsapi-google.m-cloud.io/api/");
  CWS.jobs.set_options({org_id: "companies/<uuid>", jobdetail_path: "/job", filters: ["ats_portalid:Kenexa"], ...})
Search (no browser, no bot wall):
  GET {api}job/search?callback=CWS.jobs.jobCallback&pageSize=100&offset={n}&companyName={org_id}
      &query={kw}&customAttributeFilter=ats_portalid%3D%22Kenexa%22&orderBy=relevance%20desc
  -> CWS.jobs.jobCallback({"totalHits": N, "searchResults": [{"job": {id, ref, title, url, description (HTML),
       open_date, primary_city, primary_state, salary, employment_type, schedule, sub_category, ...}}]})
  The response already has the full description, so details need no extra request.
Fallback when the API config cannot be found: render
  https://{host}/job-search-results/?keyword={kw}[&pg=N] in Playwright and parse rows
  (div.job.jobid-{id} > .jobTitle a[href=/job/{id}/{slug}/]); detail pages (/job/{id}/{slug}/) carry JSON-LD.
The detail pages themselves answer 403 to plain requests (bot filter), so the API description is the source.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote, urlencode, urlsplit

from bs4 import BeautifulSoup

from .. import browser, http, textutil
from ..models import Listing
from .base import Adapter, AdapterError, parse_detail_html

SET_API_RE = re.compile(r"CWS\.jobs\.set_api\(\s*[\"']\s*([^\"']+)[\"']")
ORG_RE = re.compile(r"org_id\s*:\s*[\"']([^\"']+)[\"']")
FILTERS_RE = re.compile(r"filters\s*:\s*\[([^\]]*)\]")
DETAIL_PATH_RE = re.compile(r"jobdetail_path\s*:\s*[\"']([^\"']+)[\"']")
JSONP_RE = re.compile(r"^\s*[\w.$]+\((.*)\)\s*;?\s*$", re.S)


def parse_cws_config(html: str) -> dict:
    """API base, org id and sitewide filters from a CWS page's inline set_api / set_options calls."""
    out: dict = {}
    m = SET_API_RE.search(html or "")
    if m:
        out["api"] = m.group(1).strip()
    # set_options block (the jobs widget, not the map: CWS.map.set_options uses JSON with escaped slashes)
    i = (html or "").find("CWS.jobs.set_options")
    block = html[i:i + 3000] if i >= 0 else ""
    m = ORG_RE.search(block)
    if m:
        out["org_id"] = m.group(1)
    m = FILTERS_RE.search(block)
    if m:
        out["filters"] = [f.strip().strip("\"'") for f in m.group(1).split(",") if f.strip().strip("\"'")]
    m = DETAIL_PATH_RE.search(block)
    if m:
        out["detail_path"] = m.group(1)
    return out


def cws_filter_param(filters: list[str]) -> str:
    """["ats_portalid:Kenexa"] -> 'ats_portalid="Kenexa"' (the Google Talent customAttributeFilter syntax)."""
    parts = []
    for f in filters or []:
        if ":" in f:
            k, v = f.split(":", 1)
            parts.append(f'{k.strip()}="{v.strip()}"')
    return " AND ".join(parts)


def parse_cws_jsonp(text: str) -> dict:
    t = (text or "").strip()
    m = JSONP_RE.match(t)
    return json.loads(m.group(1) if m else t)


def cws_listing(job: dict, base: str, detail_path: str = "/job") -> Listing | None:
    jid = job.get("id")
    title = (job.get("title") or "").strip()
    if not jid or not title:
        return None
    url = job.get("url") or ""
    if not url or urlsplit(url).netloc != urlsplit(base).netloc:
        slug = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]", "-", f"{title} {job.get('primary_city') or ''} "
                                                                 f"{job.get('primary_state') or ''}".strip().lower()))
        url = f"{base.rstrip('/')}{detail_path}/{jid}/{slug.strip('-')}/"
    city, st = job.get("primary_city"), job.get("primary_state")
    loc = ", ".join(x for x in [city, st] if x)
    gl = job.get("google_locations") or []
    if gl and isinstance(gl[0], dict) and gl[0].get("address"):
        loc = gl[0]["address"].replace(", USA", "")
    return Listing(title=title, url=url, location_text=loc, posted_at=textutil.parse_date(job.get("open_date")),
                   external_id=str(job.get("ref") or jid),
                   snippet=textutil.html_to_text(job.get("description") or "", 1500),
                   extra={"job": job, "department": job.get("sub_category") or job.get("department") or ""})


def parse_cws_results(html: str, base: str) -> tuple[list[Listing], int | None]:
    soup = BeautifulSoup(html or "", "lxml")
    total = None
    c = soup.select_one("#live-results-counter")
    if c and c.get_text(strip=True).isdigit():
        total = int(c.get_text(strip=True))
    out: list[Listing] = []
    for row in soup.select("div.job[class*='jobid-']"):
        a = row.select_one(".jobTitle a[href]")
        if not a:
            continue
        cells = [d.get_text(" ", strip=True) for d in row.select("div[role=cell]")]
        loc = row.select_one(".joblist-location")
        href = a["href"]
        m = re.search(r"/job/(\d+)/", href)
        out.append(Listing(title=a.get_text(" ", strip=True),
                           url=base.rstrip("/") + href if href.startswith("/") else href,
                           location_text=loc.get_text(" ", strip=True) if loc else "",
                           external_id=m.group(1) if m else None,
                           extra={"department": cells[0] if cells else ""}))
    return out, total


class SymphonyAdapter(Adapter):
    platform = "symphony"
    PAGE = 100

    def _base(self) -> str:
        host = self.cfg.get("host") or urlsplit(self.search_url).netloc
        return f"https://{host}"

    def _config(self, base: str) -> dict:
        cfg = {k: self.cfg[k] for k in ("api", "org_id", "filters", "detail_path") if self.cfg.get(k)}
        if cfg.get("api") and cfg.get("org_id"):
            return cfg
        path = self.cfg.get("results_path", "/job-search-results/")
        for url in (self.search_url, f"{base}{path}"):
            if not url:
                continue
            try:
                found = parse_cws_config(self.get(url).text)
            except http.FetchError as e:
                self.note(f"config page {url}: {e}")
                continue
            if found.get("api") and found.get("org_id"):
                found.update(cfg)
                return found
        return cfg

    def _api_search(self, cfg: dict, keywords: list[str], base: str) -> list[Listing]:
        api = cfg["api"].strip()
        if api.startswith("//"):
            api = "https:" + api
        if not api.endswith("/"):
            api += "/"
        flt = cws_filter_param(cfg.get("filters") or [])
        out: dict[str, Listing] = {}
        errors = 0
        for kw in keywords:
            offset = 0
            while not self.out_of_time():
                params = {"callback": "CWS.jobs.jobCallback", "pageSize": self.PAGE, "offset": offset,
                          "companyName": cfg["org_id"], "query": kw, "orderBy": "relevance desc"}
                if flt:
                    params["customAttributeFilter"] = flt
                url = f"{api}job/search?" + urlencode(params, quote_via=quote)
                try:
                    r = self.get(url, headers={"Accept": "*/*", "Referer": f"{base}/job-search-results/",
                                               "Sec-Fetch-Dest": "script", "Sec-Fetch-Mode": "no-cors",
                                               "Sec-Fetch-Site": "cross-site"})
                    data = parse_cws_jsonp(r.text)
                except (http.FetchError, ValueError) as e:
                    errors += 1
                    self.note(f"api search '{kw}': {type(e).__name__}: {str(e)[:200]}")
                    break
                results = data.get("searchResults") or []
                total = int(data.get("totalHits") or 0)
                for res in results:
                    li = cws_listing(res.get("job") or {}, base, cfg.get("detail_path") or "/job")
                    if li:
                        out.setdefault(li.norm, li)
                offset += len(results)
                if not results or offset >= min(total, 500):
                    break
        if errors == len(keywords):
            raise AdapterError("; ".join(self.notes[-3:]))
        self.method = "api"
        return list(out.values())

    def search(self, keywords):
        base = self._base()
        cfg = self._config(base)
        if cfg.get("api") and cfg.get("org_id"):
            self.note(f"CWS jobs API {cfg['api']} org {cfg['org_id']} filters {cfg.get('filters') or []}")
            try:
                return self._api_search(cfg, keywords, base)
            except AdapterError as e:
                self.note(f"CWS API failed ({e}); falling back to rendering the results page")
        else:
            self.note("CWS API config not found in page; rendering the results page")
        return self._browser_search(keywords, base)

    def _browser_search(self, keywords, base):
        path = self.cfg.get("results_path", "/job-search-results/")
        out: dict[str, Listing] = {}
        errors = 0
        for kw in keywords:
            for page in range(1, 6):
                if self.out_of_time():
                    break
                url = f"{base}{path}?keyword={quote(kw)}" + (f"&pg={page}" if page > 1 else "")
                try:
                    res = browser.render(url, wait_selector="div.job[class*='jobid-'], #live-results-counter",
                                         settle_ms=3000)
                except Exception as e:  # noqa: BLE001
                    errors += 1
                    self.note(f"search '{kw}': {type(e).__name__}: {str(e)[:200]}")
                    break
                self.method = "playwright"
                if res.get("status") and res["status"] >= 400:
                    errors += 1
                    self.note(f"search '{kw}': HTTP {res['status']}")
                    break
                rows, total = parse_cws_results(res["html"], base)
                new = 0
                for li in rows:
                    if li.norm not in out:
                        out[li.norm] = li
                        new += 1
                if not rows or new == 0 or total is None or len(out) >= total:
                    break
        if errors == len(keywords):
            raise AdapterError("; ".join(self.notes[-3:]))
        return list(out.values())

    def details(self, listing):
        job = listing.extra.get("job")
        if job:
            return self._from_api(job, listing)
        try:
            r = self.get(listing.url)
            html = r.text
            if "/job-not-found" in r.url or "/job-expired" in r.url:
                return {"closed": True}
        except http.FetchError as e:
            if e.status in (404, 410):
                return {"closed": True}
            if not http.is_bot_block(e):
                raise
            res = browser.render(listing.url, settle_ms=800)
            if "/job-not-found" in res.get("url", "") or "/job-expired" in res.get("url", ""):
                return {"closed": True}
            if res.get("status") and res["status"] >= 400:
                raise http.FetchError(listing.url, res["status"], "playwright")
            html = res["html"]
        out = parse_detail_html(html, listing.url)
        if listing.extra.get("department") and not out.get("department"):
            out["department"] = listing.extra["department"]
        return out

    @staticmethod
    def _from_api(job: dict, listing: Listing) -> dict:
        city, st = job.get("primary_city"), job.get("primary_state")
        sched = str(job.get("schedule") or "").strip()
        out = {
            "description": textutil.html_to_text(job.get("description") or ""),
            "posted_at": textutil.parse_date(job.get("open_date")) or listing.posted_at,
            "external_id": str(job.get("ref") or job.get("id") or "") or None,
            "employment_type": textutil.normalize_employment(job.get("employment_type")),
            "salary_text": (job.get("salary") or "").strip() or None,
            "department": job.get("sub_category") or job.get("department") or None,
            "location_text": listing.location_text,
            "city": city, "state": textutil.state_abbr(st) if st else None,
            "schedule": f"{sched} hours/week" if sched.isdigit() else (sched or None),
            "shift": job.get("shift") or None,
        }
        if job.get("seo_url") and "brassring" in str(job.get("seo_url")):
            out["apply_url"] = job["seo_url"]
        return {k: v for k, v in out.items() if v not in (None, "")}
