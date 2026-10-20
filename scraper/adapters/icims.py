"""iCIMS.

Classic portal: GET https://{host}/jobs/search?ss=1&searchKeyword={kw}&in_iframe=1&pr={page}
  rows: <a class="iCIMS_Anchor" href="https://{host}/jobs/{id}/{slug}/job"> with title in <h3> or <span>.
  detail: https://{host}/jobs/{id}/{slug}/job?in_iframe=1 has JSON-LD JobPosting and .iCIMS_JobContent.

Many iCIMS customers front the portal with an Attract (Jibe) site or a Radancy TalentBrew site; the classic
portal then answers with a 1-line `window.top.location.href = 'https://jobs.example.org/...'` redirect.
We follow it and hand off to the Jibe or TalentBrew adapter for that host.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus, urlsplit

from bs4 import BeautifulSoup

from .. import http, textutil
from ..models import Listing
from .base import Adapter, AdapterError, parse_detail_html
from .jibe import JibeAdapter
from .talentbrew import TalentBrewAdapter

TOP_REDIRECT_RE = re.compile(r"window\.top\.location\.href\s*=\s*'([^']+)'")


def parse_icims_search(html: str, host: str) -> list[Listing]:
    soup = BeautifulSoup(html or "", "lxml")
    out: list[Listing] = []
    seen = set()
    for a in soup.select("a[href*='/jobs/']"):
        href = a.get("href") or ""
        m = re.search(r"/jobs/(\d+)/[^/?#]+/job", href)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        title_el = a.find(["h3", "h2"]) or a
        title = re.sub(r"^\s*Job Title\s*", "", title_el.get_text(" ", strip=True)).strip()
        row = a.find_parent(class_=re.compile(r"iCIMS_JobsTable|row|iCIMS_JobListingRow")) or a.parent
        loc = ""
        posted = None
        if row is not None:
            txt = row.get_text(" | ", strip=True)
            lm = re.search(r"(?:Location|Job Locations?)\s*\|?\s*([^|]+)", txt)
            if lm:
                loc = lm.group(1).strip()
            dm = re.search(r"(?:Posted Date|Date Posted)\s*\|?\s*([^|]+)", txt)
            if dm:
                posted = textutil.parse_date(dm.group(1))
        url = href if href.startswith("http") else f"https://{host}{href}"
        url = re.sub(r"\?.*$", "", url)
        out.append(Listing(title=title, url=url, location_text=loc, posted_at=posted, external_id=m.group(1)))
    return out


class IcimsAdapter(Adapter):
    platform = "icims"

    def _delegate(self):
        return getattr(self, "_sub", None)

    def search(self, keywords):
        host = self.cfg.get("host") or urlsplit(self.search_url).netloc
        if host and not host.endswith("icims.com"):
            # careers_search_url points at a branded front end: sniff it
            sub = self._sniff_front_end(f"https://{host}/")
            if sub:
                self._sub = sub
                res = sub.search(keywords)
                self.method, self.notes = f"{sub.platform}:{sub.method}", self.notes + sub.notes
                return res
        out: dict[str, Listing] = {}
        errors = 0
        for kw in keywords:
            for page in range(0, 10):
                if self.out_of_time():
                    break
                url = f"https://{host}/jobs/search?ss=1&searchKeyword={quote_plus(kw)}&in_iframe=1&pr={page}"
                try:
                    r = self.get(url)
                except http.FetchError as e:
                    errors += 1
                    self.note(f"search '{kw}': {e}")
                    break
                m = TOP_REDIRECT_RE.search(r.text or "")
                if m and len(r.text) < 2000:
                    target = m.group(1).replace("\\/", "/")
                    self.note(f"iCIMS portal redirects to branded site {target}")
                    sub = self._sniff_front_end(target)
                    if not sub:
                        raise AdapterError(f"iCIMS redirects to {target}, front end not recognized")
                    self._sub = sub
                    res = sub.search(keywords)
                    self.method, self.notes = f"{sub.platform}:{sub.method}", self.notes + sub.notes
                    return res
                rows = parse_icims_search(r.text, host)
                self.method = "requests"
                for li in rows:
                    out.setdefault(li.norm, li)
                if len(rows) < 10:
                    break
        if errors == len(keywords):
            raise AdapterError("; ".join(self.notes[-3:]))
        return list(out.values())

    def _sniff_front_end(self, url: str):
        try:
            r = self.get(url)
        except http.FetchError as e:
            self.note(f"front end {url}: {e}")
            return None
        html = r.text or ""
        final_host = urlsplit(r.url).netloc
        if "jibecdn.com" in html or "window._jibe" in html or 'ng-app="jibeapply"' in html:
            return JibeAdapter(self.hospital, self.deadline, host=final_host)
        if "tbcdn.talentbrew.com" in html or "/search-jobs" in html and "data-total-results" in html:
            return TalentBrewAdapter(self.hospital, self.deadline, host=final_host)
        return None

    def details(self, listing):
        sub = self._delegate()
        if sub is not None:
            return sub.details(listing)
        url = listing.url + ("&" if "?" in listing.url else "?") + "in_iframe=1"
        try:
            r = self.get(url)
        except http.FetchError as e:
            if e.status in (404, 410):
                return {"closed": True}
            raise
        out = parse_detail_html(r.text, listing.url)
        if not out.get("description") or len(out["description"]) < 200:
            soup = BeautifulSoup(r.text, "lxml")
            parts = soup.select(".iCIMS_InfoMsg_Job, .iCIMS_JobContent, .iCIMS_Expandable_Text")
            if parts:
                out["description"] = textutil.html_to_text("".join(str(p) for p in parts))
        return out
