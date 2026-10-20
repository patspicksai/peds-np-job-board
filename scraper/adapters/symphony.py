"""Symphony Talent CWS (WordPress "cws" career sites backed by jobsapi-google.m-cloud.io), e.g. jobs.bostonchildrens.org.

The search results are rendered client-side by the CWS widget, so we render
https://{host}/job-search-results/?keyword={kw}[&pg=N] in Playwright and parse rows:
  div.job.jobid-{id} > .jobTitle a[href=/job/{id}/{slug}/]   + following role=cell divs (department, location)
Detail pages (/job/{id}/{slug}/) are server rendered with JSON-LD JobPosting.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

from bs4 import BeautifulSoup

from .. import browser, http
from ..models import Listing
from .base import Adapter, AdapterError, parse_detail_html


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

    def search(self, keywords):
        host = self.cfg.get("host") or urlsplit(self.search_url).netloc
        base = f"https://{host}"
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
        try:
            r = self.get(listing.url)
        except http.FetchError as e:
            if e.status in (404, 410):
                return {"closed": True}
            raise
        if "/job-not-found" in r.url or "/job-expired" in r.url:
            return {"closed": True}
        out = parse_detail_html(r.text, listing.url)
        if listing.extra.get("department") and not out.get("department"):
            out["department"] = listing.extra["department"]
        return out
