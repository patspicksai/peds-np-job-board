"""Generic adapter for unknown / custom career sites (and the fallback for every other adapter).

1. requests: fetch careers_search_url (and the base careers URL with ?q= / ?keywords= / ?search= appended),
   collect links whose text or href looks like an NP / APP job, follow up to N of them.
2. Playwright fallback when requests is blocked or finds nothing: render, wait for network idle, click
   "load more" / "next" up to 10 times, same extraction on the rendered DOM. XHR JSON responses that look
   like job lists are also mined for (title, url) pairs.
Detail: JSON-LD JobPosting when present, else main text.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote_plus, urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

from bs4 import BeautifulSoup

from .. import browser, http, relevance
from ..textutil import ABBRS as _ABBRS
from ..models import Listing, normalize_url
from .base import Adapter, AdapterError, parse_detail_html

JOBISH_HREF = re.compile(r"/(?:job|jobs|position|positions|posting|postings|requisition|opening|openings|vacanc|career)s?[/_-]|jobdetail|job_id=|jobid=|reqid=|/details/|/apply|[?&]jobs?=", re.I)
NAV_TEXT = re.compile(r"^(?:search|jobs|careers?|apply|home|login|sign in|benefits|view all.*|see all.*|next|previous|\d+)$", re.I)


def extract_job_links(html: str, base_url: str) -> list[Listing]:
    soup = BeautifulSoup(html or "", "lxml")
    out: dict[str, Listing] = {}
    base_host = urlsplit(base_url).netloc
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        text = a.get_text(" ", strip=True)
        if not text or len(text) > 220 or NAV_TEXT.match(text):
            h = a.find(["h1", "h2", "h3", "h4", "h5"])
            if h:
                text = h.get_text(" ", strip=True)
            if not text or NAV_TEXT.match(text):
                continue
        url = urljoin(base_url, href)
        host = urlsplit(url).netloc
        title_ok = relevance.title_is_app_candidate(text)
        if not title_ok:
            continue
        if not (JOBISH_HREF.search(url) or host != base_host):
            continue
        if re.search(r"indeed\.com|linkedin\.com|glassdoor|ziprecruiter|facebook|twitter|x\.com/", host):
            continue
        li = Listing(title=re.sub(r"\s+", " ", text), url=url, location_text=_nearby_location(a))
        out.setdefault(li.norm, li)
    return list(out.values())


_LOC_RE = re.compile(r"(?:\b\d+\s+[A-Za-z0-9 .'-]+,\s*)?([A-Za-z][A-Za-z .'-]{1,40}),\s*([A-Z]{2})\b(?:\s+\d{5})?")


def _nearby_location(a) -> str:
    """'201 LYONS AVE, NEWARK, NJ 07112' from the listing row around a job link (helps attribution / footprint)."""
    node = a
    for _ in range(4):
        node = node.parent
        if node is None:
            return ""
        text = node.get_text(" ", strip=True)
        if len(text) > 700:
            return ""
        for m in _LOC_RE.finditer(text):
            if m.group(2) in _ABBRS:
                return m.group(0).strip()
    return ""


def mine_json_for_jobs(text: str, base_url: str) -> list[Listing]:
    """Walk arbitrary JSON looking for objects with a title-ish key and a url/id-ish key."""
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001
        return []
    found: dict[str, Listing] = {}
    stack = [data]
    while stack:
        d = stack.pop()
        if isinstance(d, list):
            stack.extend(d[:500])
            continue
        if not isinstance(d, dict):
            continue
        title = next((d[k] for k in ("title", "Title", "jobTitle", "name", "postingTitle") if isinstance(d.get(k), str)), None)
        url = next((d[k] for k in ("url", "jobUrl", "applyUrl", "canonical_url", "externalUrl", "detailUrl", "link", "href")
                    if isinstance(d.get(k), str) and len(d[k]) > 4), None)
        if title and url and relevance.title_is_app_candidate(title):
            li = Listing(title=title.strip(), url=urljoin(base_url, url))
            found.setdefault(li.norm, li)
        stack.extend(v for v in d.values() if isinstance(v, (dict, list)))
    return list(found.values())


NEXT_TEXT = re.compile(r"^(?:next|next page|more|›|»|>|next\s*[›»>]+|[›»>]+)$", re.I)


def next_page_url(html: str, base_url: str) -> str | None:
    """The pagination "next" link of a server-rendered results page, if any."""
    soup = BeautifulSoup(html or "", "lxml")
    cands = soup.select("a[rel~=next][href], a.next[href], li.next a[href], a[aria-label*='next' i][href], "
                        "a.pagination-next[href], a[class*='next'][href]")
    if not cands:
        cands = [a for a in soup.find_all("a", href=True) if NEXT_TEXT.match(a.get_text(" ", strip=True) or "")]
    for a in cands:
        href = a.get("href", "").strip()
        if not href or href.startswith(("#", "javascript:")):
            continue
        u = urljoin(base_url, href)
        if normalize_url(u) != normalize_url(base_url):
            return u
    return None


def with_query(url: str, key: str, value: str) -> str:
    p = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k != key] + [(key, value)]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ""))


class GenericAdapter(Adapter):
    platform = "generic"
    MAX_PAGES = 4
    MAX_FOLLOW = 12

    def __init__(self, hospital, deadline=None, start_urls: list[str] | None = None, reason: str = ""):
        super().__init__(hospital, deadline)
        self._start = start_urls
        if reason:
            self.note(reason)

    def candidate_urls(self, keywords) -> list[str]:
        urls: list[str] = []
        if self._start:
            urls.extend(self._start)
        su = self.hospital.get("careers_search_url")
        cu = self.hospital.get("careers_url")
        if su:
            urls.append(su)
        base = cu or su
        if base:
            for key in ("q", "keywords", "keyword", "search"):
                urls.append(with_query(base, key, "nurse practitioner"))
            if cu and cu not in urls:
                urls.append(cu)
        seen, out = set(), []
        for u in urls:
            n = normalize_url(u)
            if n not in seen:
                seen.add(n)
                out.append(u)
        return out[:7]

    def search(self, keywords):
        urls = self.candidate_urls(keywords)
        if not urls:
            raise AdapterError("generic: no careers_url / careers_search_url")
        out: dict[str, Listing] = {}
        statuses = []
        blocked = False
        for u in urls[: self.MAX_PAGES + 2]:
            if self.out_of_time():
                break
            try:
                r = self.get(u)
                statuses.append(f"{r.status_code} {u}")
                self.method = "requests"
                found = extract_job_links(r.text, r.url)
                for li in found:
                    out.setdefault(li.norm, li)
                # server-rendered result lists: follow "next" pages (RWJBarnabas ?page_jobs=N, UVM, Covenant)
                html, cur, pages = r.text, r.url, 1
                while found and pages < self.MAX_FOLLOW and not self.out_of_time():
                    nxt = next_page_url(html, cur)
                    if not nxt:
                        break
                    try:
                        r2 = self.get(nxt)
                    except http.FetchError as e:
                        statuses.append(f"{e.status} {nxt}")
                        break
                    html, cur, pages = r2.text, r2.url, pages + 1
                    found = [li for li in extract_job_links(html, cur) if li.norm not in out]
                    for li in found:
                        out.setdefault(li.norm, li)
                if pages > 1:
                    self.note(f"generic: followed {pages} result pages from {u}")
            except http.FetchError as e:
                statuses.append(f"{e.status} {u}")
                if http.is_bot_block(e):
                    blocked = True
                    break
            except Exception as e:  # noqa: BLE001
                statuses.append(f"{type(e).__name__} {u}")
            if len(out) >= 5:
                break
        if not out and not self.out_of_time():
            for u in urls[:3]:
                try:
                    res = browser.render(u, click_more=True, capture_json=r"job|search|position|posting|requisition")
                except Exception as e:  # noqa: BLE001
                    statuses.append(f"playwright {type(e).__name__}: {str(e)[:120]} {u}")
                    continue
                statuses.append(f"playwright {res.get('status')} {u}")
                self.method = "playwright"
                if res.get("status") and res["status"] >= 400:
                    continue
                for li in extract_job_links(res["html"], res["url"]):
                    out.setdefault(li.norm, li)
                for (ju, jt) in res.get("json") or []:
                    for li in mine_json_for_jobs(jt, res["url"]):
                        out.setdefault(li.norm, li)
                if out:
                    break
        if not out:
            if all(not s.startswith(("200", "playwright 200")) for s in statuses):
                raise AdapterError("generic: no page reachable: " + "; ".join(statuses[-4:]))
            self.note("generic: pages reachable but no NP/APP job links found: " + "; ".join(statuses[-3:]))
        if blocked and self.method != "playwright":
            self.note("requests blocked; Playwright used")
        return list(out.values())

    def details(self, listing):
        try:
            r = self.get(listing.url)
            return parse_detail_html(r.text, listing.url)
        except http.FetchError as e:
            if e.status in (404, 410):
                return {"closed": True}
            if not http.is_bot_block(e):
                raise
        res = browser.render(listing.url)
        if res.get("status") and res["status"] >= 400:
            raise http.FetchError(listing.url, res["status"], "playwright")
        return parse_detail_html(res["html"], listing.url)
