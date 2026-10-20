"""Radancy TalentBrew career sites (e.g. jobs.hackensackmeridianhealth.org, jobs.geisinger.org).

Search: GET https://{host}/search-jobs/results?ActiveFacetID=0&CurrentPage={n}&RecordsPerPage=50&Distance=50
        &RadiusUnitType=0&Keywords={kw}&Location=&ShowRadius=False&IsPagination=True&...&SearchType=5
        (X-Requested-With: XMLHttpRequest) -> {"results": "<section id=search-results-list>...</section>", ...}
        Rows: <a href="/job/{city}/{slug}/{orgId}/{jobId}" data-job-id=...><h2>Title</h2>
              <span class="job-facility">..</span><span class="job-location">..</span></a>
Fallback: GET https://{host}/search-jobs/{kw} (server rendered, same markup, 15 rows).
Detail: posting page with JSON-LD JobPosting.
"""
from __future__ import annotations

import re
from urllib.parse import quote, quote_plus, urlsplit

from bs4 import BeautifulSoup

from .. import http
from ..models import Listing
from .base import Adapter, AdapterError


def parse_results_html(fragment: str, base: str) -> tuple[list[Listing], int | None]:
    soup = BeautifulSoup(fragment or "", "lxml")
    sec = soup.select_one("#search-results-list") or soup
    total = None
    tot = soup.select_one("[data-total-results]")
    if tot is not None:
        try:
            total = int(tot.get("data-total-results"))
        except (TypeError, ValueError):
            total = None
    out: list[Listing] = []
    for a in sec.select("a[href*='/job/']"):
        href = a.get("href") or ""
        h = a.find(["h2", "h3"])
        title = (h.get_text(" ", strip=True) if h else a.get_text(" ", strip=True)).strip()
        if not title or not re.search(r"/job/", href):
            continue
        fac = a.select_one(".job-facility")
        loc = a.select_one(".job-location")
        date = a.select_one(".job-date-posted, .job-date")
        location = ", ".join(x for x in [fac.get_text(" ", strip=True) if fac else "",
                                          loc.get_text(" ", strip=True) if loc else ""] if x)
        from .. import textutil
        out.append(Listing(title=title, url=base.rstrip("/") + href if href.startswith("/") else href,
                           location_text=location, external_id=a.get("data-job-id"),
                           posted_at=textutil.parse_date(date.get_text(strip=True)) if date else None,
                           extra={"facility": fac.get_text(" ", strip=True) if fac else ""}))
    return out, total


class TalentBrewAdapter(Adapter):
    platform = "talentbrew"
    PER = 50

    def __init__(self, hospital, deadline=None, host: str | None = None):
        super().__init__(hospital, deadline)
        self._host = host or self.cfg.get("talentbrew_host") or urlsplit(self.search_url).netloc

    def search(self, keywords):
        base = f"https://{self._host}"
        out: dict[str, Listing] = {}
        errors = 0
        for kw in keywords:
            page, got_any = 1, False
            while not self.out_of_time() and page <= 10:
                url = (f"{base}/search-jobs/results?ActiveFacetID=0&CurrentPage={page}&RecordsPerPage={self.PER}"
                       f"&Distance=50&RadiusUnitType=0&Keywords={quote_plus(kw)}&Location=&ShowRadius=False"
                       f"&IsPagination=True&CustomFacetName=&FacetTerm=&FacetType=0&SearchResultsModuleName=Search+Results"
                       f"&SearchFiltersModuleName=Search+Filters&SortCriteria=0&SortDirection=0&SearchType=5"
                       f"&PostalCode=&fc=&fl=&fcf=&afc=&afl=&afcf=")
                try:
                    r = self.get(url, headers={"X-Requested-With": "XMLHttpRequest",
                                               "Accept": "application/json, text/javascript, */*; q=0.01",
                                               "Referer": f"{base}/search-jobs"})
                    data = r.json()
                    rows, total = parse_results_html(data.get("results") or "", base)
                    self.method = "api"
                except (http.FetchError, ValueError) as e:
                    self.note(f"results api '{kw}' p{page}: {e}; falling back to search page")
                    try:
                        r = self.get(f"{base}/search-jobs/{quote(kw)}")
                        rows, total = parse_results_html(r.text, base)
                        self.method = "requests"
                    except http.FetchError as e2:
                        errors += 1
                        self.note(f"search '{kw}': {e2}")
                        break
                    for li in rows:
                        out.setdefault(li.norm, li)
                    break
                for li in rows:
                    out.setdefault(li.norm, li)
                got_any = got_any or bool(rows)
                if not rows or (total is not None and page * self.PER >= total):
                    break
                page += 1
        if errors == len(keywords):
            raise AdapterError("; ".join(self.notes[-3:]))
        return list(out.values())
