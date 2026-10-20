"""Oracle Taleo Enterprise (legacy) career sections: https://{tenant}.taleo.net/careersection/{section}/jobsearch.ftl

Search: POST https://{host}/careersection/rest/jobboard/searchjobs?lang=en&portal={portal}
        {"multilineEnabled":false,"sortingSelection":{"sortBySelectionParam":"3","ascendingSortingOrder":"false"},
         "fieldData":{"fields":{"KEYWORD":kw,"LOCATION":"","CATEGORY":""},"valid":true},"filterSelectionParam":{...},
         "advancedSearchFiltersSelectionParam":{...},"pageNo":N}
        -> {"requisitionList":[{"jobId","contestNo","column":[title, location, posted,...]}], "pagingData":{"totalCount"}}
        The numeric portal id is in the career section page (`portal=\\d+` / "portalNo").
Fallback: Playwright render of jobsearch.ftl?keyword=kw and parse jobdetail.ftl?job= links.
Detail: https://{host}/careersection/{section}/jobdetail.ftl?job={contestNo} (Playwright; content is JS-built).
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus, urlsplit

from bs4 import BeautifulSoup

from .. import browser, http, textutil
from ..models import Listing
from .base import Adapter, AdapterError, parse_detail_html

PORTAL_RE = re.compile(r"portal(?:No)?['\"]?\s*[:=]\s*['\"]?(\d{6,})")


class TaleoAdapter(Adapter):
    platform = "taleo"

    def _conf(self):
        host = self.cfg.get("host") or urlsplit(self.search_url).netloc
        sec = self.cfg.get("career_section")
        if not sec:
            m = re.search(r"/careersection/([^/]+)/", self.search_url or "")
            sec = m.group(1) if m else "ext"
        return host, sec

    def search(self, keywords):
        host, sec = self._conf()
        self._host, self._sec = host, sec
        out: dict[str, Listing] = {}
        portal = self.cfg.get("portal")
        if not portal:
            try:
                html = self.get(f"https://{host}/careersection/{sec}/jobsearch.ftl?lang=en").text
                m = PORTAL_RE.search(html)
                portal = m.group(1) if m else None
            except http.FetchError as e:
                self.note(f"career section page: {e}")
        errors = 0
        for kw in keywords:
            rows = None
            if portal:
                try:
                    rows = self._rest(host, sec, portal, kw)
                    self.method = "api"
                except Exception as e:  # noqa: BLE001
                    self.note(f"rest search '{kw}': {e}")
            if rows is None:
                try:
                    rows = self._render(host, sec, kw)
                    self.method = "playwright"
                except Exception as e:  # noqa: BLE001
                    errors += 1
                    self.note(f"search '{kw}': {type(e).__name__}: {e}")
                    continue
            for li in rows:
                out.setdefault(li.norm, li)
        if errors == len(keywords):
            raise AdapterError("; ".join(self.notes[-3:]))
        return list(out.values())

    def _rest(self, host, sec, portal, kw):
        rows: list[Listing] = []
        for page in range(1, 6):
            body = {"multilineEnabled": False,
                    "sortingSelection": {"sortBySelectionParam": "3", "ascendingSortingOrder": "false"},
                    "fieldData": {"fields": {"KEYWORD": kw, "LOCATION": "", "CATEGORY": ""}, "valid": True},
                    "filterSelectionParam": {"searchFilterSelections": []},
                    "advancedSearchFiltersSelectionParam": {"searchFilterSelections": []},
                    "pageNo": page}
            data = self.post_json(f"https://{host}/careersection/rest/jobboard/searchjobs?lang=en&portal={portal}", body,
                                  headers={"Referer": f"https://{host}/careersection/{sec}/jobsearch.ftl",
                                           "tz": "GMT-04:00", "tzname": "America/New_York"})
            reqs = data.get("requisitionList") or []
            for r in reqs:
                cols = r.get("column") or []
                contest = r.get("contestNo") or r.get("jobId")
                title = cols[0] if cols else ""
                loc = cols[1] if len(cols) > 1 else ""
                if isinstance(loc, list):
                    loc = "; ".join(loc)
                posted = next((textutil.parse_date(c) for c in cols[2:] if isinstance(c, str) and re.search(r"\d{4}|\w{3} \d", c)), None)
                rows.append(Listing(title=str(title).strip(), url=f"https://{host}/careersection/{sec}/jobdetail.ftl?job={contest}",
                                    location_text=str(loc).strip('[]"'), posted_at=posted, external_id=str(contest)))
            total = int(((data.get("pagingData") or {}).get("totalCount")) or 0)
            if not reqs or page * 25 >= total:
                break
        return rows

    def _render(self, host, sec, kw):
        res = browser.render(f"https://{host}/careersection/{sec}/jobsearch.ftl?lang=en&keyword={quote_plus(kw)}",
                             wait_selector="a[href*='jobdetail.ftl']")
        soup = BeautifulSoup(res["html"], "lxml")
        rows = []
        for a in soup.select("a[href*='jobdetail.ftl']"):
            href = a.get("href") or ""
            m = re.search(r"job=([A-Za-z0-9-]+)", href)
            if not m:
                continue
            rows.append(Listing(title=a.get_text(" ", strip=True),
                                url=f"https://{host}/careersection/{sec}/jobdetail.ftl?job={m.group(1)}",
                                external_id=m.group(1)))
        return rows

    def details(self, listing):
        res = browser.render(listing.url, wait_selector=".editablesection, #requisitionDescriptionInterface")
        out = parse_detail_html(res["html"], listing.url)
        soup = BeautifulSoup(res["html"], "lxml")
        sec = soup.select_one("#requisitionDescriptionInterface, .editablesection")
        if sec is not None:
            out["description"] = textutil.html_to_text(str(sec))
        return out
