"""Jibe (iCIMS Attract / "careers-home") career sites, e.g. jobs.wakemed.org, jobs.novanthealth.org.

Search: GET https://{host}/api/jobs?keywords={kw}&page={n}&limit=100&sortBy=relevance&descending=false&internal=false
        -> {"totalCount": int, "jobs": [{"data": {slug, req_id, title, description, posted_date, apply_url,
            full_location, city, state, employment_type, tags*, category, meta_data.canonical_url, ...}}]}
Public posting URL: https://{host}/jobs/{slug}  (canonical_url when present, minus ?lang)
Detail: the search payload already carries the full description; the posting page also has JSON-LD.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

from .. import http, textutil
from ..models import Listing
from .base import Adapter, AdapterError, parse_detail_html


class JibeAdapter(Adapter):
    platform = "jibe"
    LIMIT = 100

    def __init__(self, hospital, deadline=None, host: str | None = None):
        super().__init__(hospital, deadline)
        self._host = host or self.cfg.get("jibe_host") or urlsplit(self.search_url).netloc

    def search(self, keywords):
        self.method = "api"
        out: dict[str, Listing] = {}
        errors = 0
        for kw in keywords:
            page, total = 1, None
            while not self.out_of_time() and page <= 10:
                url = (f"https://{self._host}/api/jobs?keywords={quote(kw)}&page={page}&limit={self.LIMIT}"
                       f"&sortBy=relevance&descending=false&internal=false")
                try:
                    data = self.get_json(url, headers={"Referer": f"https://{self._host}/jobs"})
                except http.FetchError as e:
                    errors += 1
                    self.note(f"search '{kw}': {e}")
                    break
                except ValueError as e:
                    errors += 1
                    self.note(f"search '{kw}': non-JSON response ({e})")
                    break
                jobs = data.get("jobs") or []
                if total is None:
                    total = int(data.get("totalCount") or data.get("count") or 0)
                for j in jobs:
                    d = j.get("data", j)
                    li = self._listing(d)
                    if li:
                        out.setdefault(li.norm, li)
                if not jobs or page * self.LIMIT >= (total or 0):
                    break
                page += 1
        if errors == len(keywords):
            raise AdapterError("; ".join(self.notes[-3:]))
        return list(out.values())

    def _listing(self, d: dict) -> Listing | None:
        slug = d.get("slug") or d.get("req_id")
        if not slug:
            return None
        canon = ((d.get("meta_data") or {}).get("canonical_url") or "")
        url = re.sub(r"\?lang=[a-z-]+$", "", canon) if canon else f"https://{self._host}/jobs/{slug}"
        loc = d.get("full_location") or ", ".join(x for x in [d.get("city"), d.get("state")] if x)
        desc_parts = [d.get("description") or ""]
        for k in ("responsibilities", "qualifications"):
            if d.get(k):
                desc_parts.append(d[k])
        return Listing(
            title=(d.get("title") or "").strip(), url=url, location_text=loc,
            posted_at=textutil.parse_date(d.get("posted_date")), external_id=str(d.get("req_id") or slug),
            snippet=" ".join(desc_parts)[:4000],
            extra={"data": d},
        )

    def details(self, listing):
        d = listing.extra.get("data") or {}
        out: dict = {}
        try:
            r = self.get(listing.url)
            out = parse_detail_html(r.text, listing.url)
        except http.FetchError as e:
            if e.status in (404, 410):
                return {"closed": True}
            self.note(f"detail page {e}")
        if not out.get("description") or len(out["description"]) < 200:
            parts = [d.get("description") or ""]
            for k in ("responsibilities", "qualifications"):
                if d.get(k):
                    parts.append(f"{k.title()}\n{d[k]}")
            out["description"] = textutil.html_to_text("\n\n".join(parts))
        out.setdefault("posted_at", textutil.parse_date(d.get("posted_date")) or listing.posted_at)
        out["external_id"] = str(d.get("req_id") or listing.external_id or "") or None
        if d.get("apply_url"):
            out["apply_url"] = d["apply_url"]
        tags = " ".join(str(x) for k, v in d.items() if k.startswith("tags") and isinstance(v, list) for x in v)
        et = d.get("employment_type") or tags
        out["employment_type"] = textutil.normalize_employment(et) if et else out.get("employment_type")
        out["shift"] = out.get("shift") or textutil.guess_shift(tags)
        out["schedule"] = out.get("schedule") or textutil.guess_schedule(tags)
        if d.get("city"):
            out["city"] = d.get("city")
        st = textutil.state_abbr(d.get("state"))
        if st:
            out["state"] = st
        cats = d.get("category") or []
        if d.get("location_name"):
            out["department"] = d.get("location_name")
        elif cats:
            out["department"] = str(cats[0]).strip()
        return out
