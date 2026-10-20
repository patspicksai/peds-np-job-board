"""Adapter interface.

search(hospital, keywords) -> list[Listing]   (raise on hard failure; return [] for a reachable empty board)
details(listing, hospital) -> dict            (Job field overrides: description, posted_at, external_id, ...)

Adapters record per-keyword problems in self.notes so the run report can explain a zero.
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urljoin, urlsplit

from .. import config, http, textutil
from ..models import Listing


class AdapterError(Exception):
    pass


class Adapter:
    platform = "base"

    def __init__(self, hospital: dict, deadline: float | None = None):
        self.hospital = hospital
        self.cfg: dict[str, Any] = dict(hospital.get("ats_config") or {})
        self.notes: list[str] = []
        self.requests = 0
        self.deadline = deadline or (time.time() + config.HOSPITAL_BUDGET_SECONDS)
        self.method = ""  # e.g. "api", "requests", "playwright" (reported)

    # -- helpers
    def out_of_time(self) -> bool:
        return time.time() > self.deadline

    def note(self, msg: str) -> None:
        if msg not in self.notes:
            self.notes.append(msg[:400])

    @property
    def search_url(self) -> str:
        return self.hospital.get("careers_search_url") or self.hospital.get("careers_url") or ""

    @property
    def host(self) -> str:
        h = self.cfg.get("host")
        if h:
            return h
        return urlsplit(self.search_url).netloc

    def abs(self, href: str, base: str | None = None) -> str:
        return urljoin(base or f"https://{self.host}/", href)

    def get(self, url, **kw):
        self.requests += 1
        return http.get(url, **kw)

    def get_json(self, url, **kw):
        self.requests += 1
        return http.get_json(url, **kw)

    def post_json(self, url, payload, **kw):
        self.requests += 1
        return http.post_json(url, payload, **kw)

    # -- interface
    def search(self, keywords: list[str]) -> list[Listing]:
        raise NotImplementedError

    def details(self, listing: Listing) -> dict:
        """Default: fetch the posting page; JSON-LD first, then main text."""
        r = self.get(listing.url, ok=(200,))
        return parse_detail_html(r.text, listing.url)


def parse_detail_html(html: str, url: str) -> dict:
    out: dict[str, Any] = {}
    jp = textutil.jsonld_jobposting(html)
    if jp:
        out.update(textutil.jobposting_fields(jp))
    if not out.get("description") or len(out.get("description", "")) < 200:
        out["description"] = textutil.page_main_text(html)
    low = (html or "")[:200000].lower()
    if any(p in low for p in ("no longer available", "no longer accepting", "position has been filled",
                              "job has expired", "this job is closed", "job-expired", "posting has closed")) and not jp:
        out["closed"] = True
    return out
