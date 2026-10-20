"""SAP SuccessFactors Recruiting Marketing (RMK) career sites, e.g. careers.duke.edu, jobs.jhu.edu.

Search: GET https://{host}/search/?q={kw}&sortColumn=referencedate&sortOrder=desc&startrow={n}
        rows: tr.data-row > a.jobTitle-link[href=/job/...], span.jobLocation, span.jobDate, span.jobFacility
        paging: span.paginationLabel "Results 1 - 25 of 670"
Detail: /job/{slug}/{id}/ with microdata (itemprop=title/datePosted/description), span.jobdescription,
        data-careersite-propertyid fields; JSON-LD on some tenants.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus, urlsplit

from bs4 import BeautifulSoup

from .. import browser, http, textutil
from ..models import Listing
from .base import Adapter, AdapterError, parse_detail_html


def parse_sf_search(html: str, base: str) -> tuple[list[Listing], int | None]:
    soup = BeautifulSoup(html or "", "lxml")
    total = None
    lab = soup.select_one(".paginationLabel")
    if lab:
        m = re.search(r"of\s+([\d,]+)", lab.get_text(" ", strip=True))
        if m:
            total = int(m.group(1).replace(",", ""))
    out: list[Listing] = []
    seen = set()
    for row in soup.select("tr.data-row, li.job-tile, div.job-tile"):
        a = row.select_one("a.jobTitle-link") or row.select_one("a[href*='/job/']")
        if not a:
            continue
        href = a.get("href") or ""
        if href in seen:
            continue
        seen.add(href)
        loc = row.select_one(".colLocation .jobLocation, span.jobLocation, .job-location")
        date = row.select_one(".colDate .jobDate, span.jobDate, .job-date")
        fac = row.select_one(".jobFacility, .colFacility")
        mid = re.search(r"/(\d{6,})/?$", href)
        out.append(Listing(
            title=a.get_text(" ", strip=True), url=base.rstrip("/") + href if href.startswith("/") else href,
            location_text=re.sub(r"\s+", " ", loc.get_text(" ", strip=True)) if loc else "",
            posted_at=textutil.parse_date(date.get_text(" ", strip=True)) if date else None,
            external_id=mid.group(1) if mid else None,
            extra={"facility": fac.get_text(" ", strip=True) if fac else ""}))
    return out, total


def parse_sf_detail(html: str, url: str) -> dict:
    out = parse_detail_html(html, url)
    soup = BeautifulSoup(html or "", "lxml")
    desc = soup.select_one("span.jobdescription, [itemprop=description] .jobdescription, [itemprop=description]")
    if desc is not None:
        out["description"] = textutil.html_to_text(str(desc))
    dp = soup.select_one("meta[itemprop=datePosted]")
    if dp and dp.get("content"):
        out["posted_at"] = textutil.parse_date(dp["content"]) or out.get("posted_at")
    fields = {}
    for el in soup.select("[data-careersite-propertyid]"):
        k = el.get("data-careersite-propertyid")
        v = el.get_text(" ", strip=True)
        if k and v and k not in fields:
            fields[k] = v
    if not out.get("posted_at") and fields.get("date"):
        out["posted_at"] = textutil.parse_date(fields["date"])
    labels = {}
    for tok in soup.select(".joblayouttoken"):
        lab = tok.select_one(".joblayouttoken-label")
        val = tok.select_one("[data-careersite-propertyid]")
        if lab and val:
            labels[lab.get_text(" ", strip=True).rstrip(":").lower()] = val.get_text(" ", strip=True)
    for key in ("requisition number", "req id", "job id", "requisition id"):
        if labels.get(key):
            out["external_id"] = labels[key]
            break
    for key in ("department", "dept", "facility", "unit", "department name"):
        if labels.get(key):
            out["department"] = labels[key]
            break
    for key in ("shift", "shift type"):
        if labels.get(key):
            out["shift"] = labels[key]
    for key in ("employment type", "job type", "full/part time", "work schedule"):
        if labels.get(key):
            out["employment_type"] = textutil.normalize_employment(labels[key])
    loc = soup.select_one("[itemprop=jobLocation] [itemprop=addressLocality]")
    reg = soup.select_one("meta[itemprop=addressRegion]")
    if loc is not None:
        out["city"] = loc.get("content") or loc.get_text(strip=True)
    if reg is not None:
        out["state"] = textutil.state_abbr(reg.get("content"))
    return out


class SuccessFactorsAdapter(Adapter):
    platform = "successfactors"
    PER = 25

    def search(self, keywords):
        host = self.cfg.get("host") or urlsplit(self.search_url).netloc
        base = f"https://{host}"
        self._base = base
        out: dict[str, Listing] = {}
        errors = 0
        blocked = False
        for kw in keywords:
            start = 0
            while not self.out_of_time():
                url = f"{base}/search/?q={quote_plus(kw)}&sortColumn=referencedate&sortOrder=desc&startrow={start}"
                html = None
                if not blocked:
                    try:
                        html = self.get(url).text
                        self.method = "requests"
                    except http.FetchError as e:
                        if http.is_bot_block(e):
                            blocked = True
                            self.note(f"requests blocked ({e.status}); using Playwright")
                        else:
                            errors += 1
                            self.note(f"search '{kw}': {e}")
                            break
                if html is None:
                    res = browser.render(url, wait_selector="tr.data-row, .paginationLabel")
                    if res.get("status") and res["status"] >= 400:
                        errors += 1
                        self.note(f"search '{kw}' (playwright): HTTP {res['status']} {res.get('title', '')[:60]}")
                        break
                    html = res["html"]
                    self.method = "playwright"
                rows, total = parse_sf_search(html, base)
                for li in rows:
                    out.setdefault(li.norm, li)
                start += self.PER
                if not rows or total is None or start >= min(total, 250):
                    break
        if errors == len(keywords):
            raise AdapterError("; ".join(self.notes[-3:]))
        return list(out.values())

    def details(self, listing):
        try:
            html = self.get(listing.url).text
        except http.FetchError as e:
            if e.status in (404, 410):
                return {"closed": True}
            if http.is_bot_block(e):
                html = browser.render(listing.url)["html"]
            else:
                raise
        return parse_sf_detail(html, listing.url)
