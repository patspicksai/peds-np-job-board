"""Johns Hopkins Medicine custom board (jobs.hopkinsmedicine.org; postings sourced from SuccessFactors).

Search: GET https://{host}/jobs/{category}/?search_term={kw}&page_jobs={n}
        (category "advanced-practice-providers" and "nurse-practitioner"; 20 rows/page)
  rows: <a href="/job/{id}/{slug}/"><h3>Title</h3></a><dl> job-ref (Requisition #), location, category,
        schedule, employment_type </dl>
  count: <h2>Found <strong>58</strong> ...
Detail: /job/{id}/{slug}/ has JSON-LD JobPosting (description, datePosted, identifier, address).
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus, urlsplit

from bs4 import BeautifulSoup

from .. import http
from ..models import Listing
from .base import Adapter, AdapterError, parse_detail_html


def parse_hopkins(html: str, base: str) -> tuple[list[Listing], int | None]:
    soup = BeautifulSoup(html or "", "lxml")
    total = None
    f = soup.select_one(".search-headings h2 strong")
    if f and f.get_text(strip=True).isdigit():
        total = int(f.get_text(strip=True))
    out = []
    for a in soup.select("#job-list-section a[href*='/job/']"):
        h3 = a.find("h3")
        if not h3:
            continue
        box = a.parent
        def dd(cls):
            el = box.select_one(f".{cls} dd") if box else None
            return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""
        out.append(Listing(title=h3.get_text(" ", strip=True), url=base.rstrip("/") + a["href"],
                           location_text=dd("location"), external_id=dd("job-ref") or None,
                           extra={"schedule": dd("schedule"), "employment_type": dd("employment_type"),
                                  "category": dd("category")}))
    return out, total


class HopkinsAdapter(Adapter):
    platform = "hopkins"
    CATEGORIES = ("advanced-practice-providers", "nurse-practitioner")

    def search(self, keywords):
        host = self.cfg.get("host") or urlsplit(self.search_url).netloc or "jobs.hopkinsmedicine.org"
        base = f"https://{host}"
        self.method = "requests"
        out: dict[str, Listing] = {}
        errors, attempts = 0, 0
        # Category pages already scope to APPs/NPs; run each with an empty term plus the peds-specific terms.
        terms = [""] + [k for k in keywords if k.lower() not in ("nurse practitioner", "advanced practice provider")]
        for cat in self.cfg.get("categories", self.CATEGORIES):
            for kw in terms:
                for page in range(1, 8):
                    if self.out_of_time():
                        break
                    attempts += 1
                    url = f"{base}/jobs/{cat}/?" + (f"search_term={quote_plus(kw)}&" if kw else "") + f"page_jobs={page}"
                    try:
                        r = self.get(url)
                    except http.FetchError as e:
                        errors += 1
                        self.note(f"{cat} '{kw}': {e}")
                        break
                    rows, total = parse_hopkins(r.text, base)
                    for li in rows:
                        out.setdefault(li.norm, li)
                    if not rows or total is None or page * 20 >= total:
                        break
        if attempts and errors == attempts:
            raise AdapterError("; ".join(self.notes[-3:]))
        return list(out.values())

    def details(self, listing):
        try:
            r = self.get(listing.url)
        except http.FetchError as e:
            if e.status in (404, 410):
                return {"closed": True}
            raise
        out = parse_detail_html(r.text, listing.url)
        ex = listing.extra
        if ex.get("schedule"):
            out.setdefault("shift", ex["schedule"])
        if ex.get("employment_type") and not out.get("employment_type"):
            from .. import textutil
            out["employment_type"] = textutil.normalize_employment(ex["employment_type"])
        return out
