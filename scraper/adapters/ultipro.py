"""UKG Pro (UltiPro) Recruiting job boards.

Board URL: https://recruiting.ultipro.com/{COMPANY}/JobBoard/{board-guid}/  (ats_config.board_path = "/{COMPANY}/JobBoard/{guid}")
Search: POST https://recruiting.ultipro.com{board_path}/JobBoardView/LoadSearchResults
        {"opportunitySearch": {"Top": 50, "Skip": N, "QueryString": kw, "OrderBy": [{"Value": "postedDateDesc",
          "PropertyName": "PostedDate", "Ascending": false}], "Filters": [...]}, "matchCriteria": {...}}
        -> {"opportunities": [{"Id","Title","RequisitionNumber","PostedDate","FullTime","Locations":[{"Address":{...}}],
            "BriefDescription"}], "totalCount": int}
Detail: GET https://recruiting.ultipro.com{board_path}/OpportunityDetail?opportunityId={Id}
        (HTML; the opportunity JSON is embedded as `new US.Opportunity.CandidateOpportunityDetail({...})`)
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from .. import http, textutil
from ..models import Listing
from .base import Adapter, AdapterError

DETAIL_RE = re.compile(r"CandidateOpportunityDetail\((\{.*?\})\)\s*;", re.S)


def board_path_from_url(url: str) -> str | None:
    m = re.search(r"(/[A-Z0-9]+/JobBoard/[0-9a-fA-F-]{36})", url or "")
    return m.group(1) if m else None


def parse_detail_json(html: str) -> dict:
    m = DETAIL_RE.search(html or "")
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except Exception:  # noqa: BLE001
        return {}


class UltiproAdapter(Adapter):
    platform = "ultipro"
    TOP = 50

    def search(self, keywords):
        host = self.cfg.get("host") or "recruiting.ultipro.com"
        bp = self.cfg.get("board_path") or board_path_from_url(self.search_url) or \
            board_path_from_url(self.hospital.get("careers_url") or "")
        if not bp:
            raise AdapterError("ultipro: board_path unknown (need /{COMPANY}/JobBoard/{guid})")
        self._base = f"https://{host}{bp.rstrip('/')}"
        self.method = "api"
        out: dict[str, Listing] = {}
        errors = 0
        for kw in keywords:
            skip = 0
            while not self.out_of_time():
                payload = {"opportunitySearch": {"Top": self.TOP, "Skip": skip, "QueryString": kw,
                                                 "OrderBy": [{"Value": "postedDateDesc", "PropertyName": "PostedDate",
                                                              "Ascending": False}], "Filters": []},
                           "matchCriteria": {"PreferredJobs": [], "Educations": [], "LicenseAndCertifications": [],
                                             "Skills": [], "hasNoLicenses": False, "SkippedSkills": []}}
                try:
                    data = self.post_json(f"{self._base}/JobBoardView/LoadSearchResults", payload,
                                          headers={"Referer": f"{self._base}/", "X-Requested-With": "XMLHttpRequest"})
                except (http.FetchError, ValueError) as e:
                    errors += 1
                    self.note(f"search '{kw}': {e}")
                    break
                opps = data.get("opportunities") or []
                total = int(data.get("totalCount") or 0)
                for o in opps:
                    oid = o.get("Id")
                    if not oid:
                        continue
                    locs = o.get("Locations") or []
                    loc = ""
                    if locs:
                        a = (locs[0].get("Address") or {})
                        st = (a.get("State") or {}).get("Code") if isinstance(a.get("State"), dict) else a.get("State")
                        loc = ", ".join(x for x in [locs[0].get("LocalizedDescription"), a.get("City"), st] if x)
                    li = Listing(title=(o.get("Title") or "").strip(),
                                 url=f"{self._base}/OpportunityDetail?opportunityId={oid}",
                                 location_text=loc, posted_at=textutil.parse_date(o.get("PostedDate")),
                                 external_id=o.get("RequisitionNumber"), snippet=o.get("BriefDescription") or "",
                                 extra={"opp": o})
                    out.setdefault(li.norm, li)
                skip += self.TOP
                if not opps or skip >= min(total, 300):
                    break
        if errors == len(keywords):
            raise AdapterError("; ".join(self.notes[-3:]))
        return list(out.values())

    def details(self, listing):
        r = self.get(listing.url)
        d = parse_detail_json(r.text)
        o = listing.extra.get("opp") or {}
        desc = d.get("Description") or o.get("BriefDescription") or ""
        out = {"description": textutil.html_to_text(desc),
               "external_id": d.get("RequisitionNumber") or listing.external_id,
               "posted_at": textutil.parse_date(d.get("PostedDate")) or listing.posted_at}
        ft = d.get("FullTime", o.get("FullTime"))
        if ft is not None:
            out["employment_type"] = "Full time" if ft else "Part time"
        if d.get("JobCategoryName"):
            out["department"] = d.get("JobCategoryName")
        return out
