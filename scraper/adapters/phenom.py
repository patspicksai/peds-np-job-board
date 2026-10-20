"""Phenom People career sites (e.g. careers.chop.edu/us/en, careers.nyp.org/us/en, careers.vcuhealth.org/us/en).

Verified live (CHOP): the search page is server-rendered with the first results embedded in
  phApp.ddo = {"eagerLoadRefineSearch": {"totalHits": N, "data": {"jobs": [...]}}, ...};
so GET https://{host}/{locale}/search-results?keywords={kw}&from={n} works with plain requests.
Each job: jobId, reqId, title, location, city, state, postedDate, type, category, descriptionTeaser,
          applyUrl (often the underlying Workday/Taleo apply link), jobSeqNo.
The page pages by `from` in steps of 10 (default page size).

Also tried first: POST https://{host}/widgets {"ddoKey": "refineSearch", "refNum": ..., "pageId": ...,
  "keywords": kw, "from": n, "size": 50, ...} -> {"refineSearch": {...same shape...}} (fewer requests).
Detail: https://{host}/{locale}/job/{jobId}/{slug} page with JSON-LD JobPosting and phApp.ddo.jobDetail.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote, urlsplit

from .. import browser, http, textutil
from ..models import Listing
from .base import Adapter, AdapterError, parse_detail_html

DDO_RE = re.compile(r"phApp\.ddo\s*=\s*(\{.*?\});\s*(?:phApp\.|</script>)", re.S)
REFNUM_RE = re.compile(r'"refNum"\s*:\s*"([A-Z0-9]+)"')
PAGEID_RE = re.compile(r'"pageId"\s*:\s*"([a-z0-9-]+)"')


def parse_phenom_jobs(payload: dict) -> tuple[list[dict], int | None]:
    for key in ("refineSearch", "eagerLoadRefineSearch"):
        rs = payload.get(key)
        if isinstance(rs, dict) and isinstance(rs.get("data"), dict):
            return rs["data"].get("jobs") or [], rs.get("totalHits")
    return [], None


def ddo_from_html(html: str) -> dict:
    m = DDO_RE.search(html or "")
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except Exception:  # noqa: BLE001
        return {}


class PhenomAdapter(Adapter):
    platform = "phenom"
    SIZE = 50

    def _locale_base(self) -> tuple[str, str]:
        host = self.cfg.get("host") or urlsplit(self.search_url).netloc
        path = urlsplit(self.search_url).path
        m = re.match(r"^/([a-z]{2})/([a-z]{2})(?:/|$)", path)
        loc = f"/{m.group(1)}/{m.group(2)}" if m else self.cfg.get("locale_path", "/us/en")
        return host, loc

    def _page(self, kw: str, start: int) -> tuple[list[dict], int | None, str]:
        url = f"https://{self._host}{self._loc}/search-results?keywords={quote(kw)}" + (f"&from={start}&s=1" if start else "")
        r = self.get(url)
        html = r.text
        if not getattr(self, "_refnum", None):
            m = REFNUM_RE.search(html)
            self._refnum = m.group(1) if m else None
            m = PAGEID_RE.search(html)
            self._pageid = m.group(1) if m else "page20"
        jobs, total = parse_phenom_jobs(ddo_from_html(html))
        return jobs, total, html

    def _widgets(self, kw: str, start: int) -> tuple[list[dict], int | None]:
        country, lang = (self._loc.strip("/").split("/") + ["us", "en"])[:2]
        body = {"lang": f"{lang}_{country}", "deviceType": "desktop", "country": country,
                "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "", "subsearch": "",
                "from": start, "irs": False, "jobs": True, "counts": True,
                "all_fields": ["category", "state", "city", "type"], "size": self.SIZE, "clearAll": False,
                "jdsource": "facets", "isSliderEnable": False, "pageId": self._pageid, "siteType": "external",
                "keywords": kw, "global": True, "selected_fields": {}, "locationData": {}}
        if self._refnum:
            body["refNum"] = self._refnum
        data = self.post_json(f"https://{self._host}/widgets", body,
                              headers={"Origin": f"https://{self._host}",
                                       "Referer": f"https://{self._host}{self._loc}/search-results?keywords={quote(kw)}"})
        return parse_phenom_jobs(data)

    def search(self, keywords):
        self._host, self._loc = self._locale_base()
        self._refnum = self.cfg.get("ref_num")
        self._pageid = "page20"
        out: dict[str, Listing] = {}
        errors = 0
        widgets_ok = True
        for kw in keywords:
            if self.out_of_time():
                break
            try:
                jobs, total, _ = self._page(kw, 0)
                self.method = self.method or "requests"
            except http.FetchError as e:
                if http.is_bot_block(e):
                    try:
                        res = browser.render(f"https://{self._host}{self._loc}/search-results?keywords={quote(kw)}")
                        jobs, total = parse_phenom_jobs(ddo_from_html(res["html"]))
                        self.method = "playwright"
                    except Exception as e2:  # noqa: BLE001
                        errors += 1
                        self.note(f"search '{kw}': {e}; playwright: {e2}")
                        continue
                else:
                    errors += 1
                    self.note(f"search '{kw}': {e}")
                    continue
            for j in jobs:
                li = self._listing(j)
                if li:
                    out.setdefault(li.norm, li)
            total = int(total or 0)
            got = len(jobs)
            # page through the rest: widgets API (50 per call) when it works, else the page (10 per call)
            while got < min(total, 300) and not self.out_of_time():
                more = None
                if widgets_ok:
                    try:
                        more, _t = self._widgets(kw, got)
                        if not more:
                            raise ValueError("empty refineSearch")
                        self.method = "requests+api"
                    except Exception as e:  # noqa: BLE001
                        widgets_ok = False
                        self.note(f"widgets api unavailable ({type(e).__name__}: {str(e)[:120]}); paging HTML")
                if more is None:
                    try:
                        more, _t, _ = self._page(kw, got)
                    except Exception as e:  # noqa: BLE001
                        self.note(f"page from={got} '{kw}': {e}")
                        break
                if not more:
                    break
                for j in more:
                    li = self._listing(j)
                    if li:
                        out.setdefault(li.norm, li)
                got += len(more)
        if errors == len(keywords):
            raise AdapterError("; ".join(self.notes[-3:]))
        return list(out.values())

    def _listing(self, j: dict) -> Listing | None:
        jid = j.get("jobId") or j.get("reqId")
        if not jid:
            return None
        slug = re.sub(r"[^A-Za-z0-9]+", "-", j.get("title") or "").strip("-")
        url = f"https://{self._host}{self._loc}/job/{jid}/{slug}" if slug else f"https://{self._host}{self._loc}/job/{jid}"
        loc = j.get("location") or ", ".join(x for x in [j.get("city"), j.get("state")] if x)
        return Listing(title=(j.get("title") or "").strip(), url=url, location_text=loc,
                       posted_at=textutil.parse_date(j.get("postedDate") or j.get("dateCreated")),
                       external_id=str(j.get("reqId") or jid), snippet=j.get("descriptionTeaser") or "",
                       extra={"job": j})

    def details(self, listing):
        try:
            html = self.get(listing.url).text
        except http.FetchError as e:
            if e.status in (404, 410):
                return {"closed": True}
            if not http.is_bot_block(e):
                raise
            html = browser.render(listing.url)["html"]
        out = parse_detail_html(html, listing.url)
        ddo = ddo_from_html(html)
        job = (((ddo.get("jobDetail") or {}).get("data") or {}).get("job") or {})
        if job.get("description") and len(out.get("description", "")) < 300:
            out["description"] = textutil.html_to_text(job["description"])
        j = listing.extra.get("job") or {}
        if j.get("type") and not out.get("employment_type"):
            out["employment_type"] = textutil.normalize_employment(j.get("type"))
        if j.get("category") and not out.get("department"):
            out["department"] = j.get("category")
        if j.get("applyUrl"):
            out["apply_url"] = j["applyUrl"]
        if job.get("isJobClosed") or (ddo.get("jobDetail") or {}).get("status") == "failure":
            out["closed"] = bool(job.get("isJobClosed"))
        return out
