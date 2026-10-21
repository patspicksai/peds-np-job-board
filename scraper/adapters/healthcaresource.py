"""HealthcareSource (Symplr) Position Manager career sites: pm.healthcaresource.com/cs/<client>/.

Used by CaroMont, Granville, Crouse, Ellis, Auburn, Conway, KidsPeace, Lawrence, Berkshire, Brockton (Signature),
Emerson (its own search page wraps the same backend) and Rutland's HCTS portal.

ats_config: client_path ("/cs/caromont") or backend_client_path, or search_api
("https://pm.healthcaresource.com/JobseekerSearchAPI/caromont/api/Search"); the client id is the path segment.

Search (verified on Actions probe 39650100056 / 39653988170, CaroMont):
  POST https://pm.healthcaresource.com/JobseekerSearchAPI/{client}/api/Search?size=100
       {"from": N, "query": {"bool": {"must": {"multi_match": {"query": kw, "fields": ["title", "description"]}}}}}
  -> Elasticsearch shape: hits.total.value, hits.hits[]._source {title, name, documentId "8023_31484", datePosted,
     employmentType, occupationalCategory, jobLocation.address.{addressLocality, addressRegion, addressLocalityRegion},
     hiringOrganization{name, subOrganization{name, subOrganization{name}}}, userArea.requisitionNumber}
  The search hit carries no description.
Detail (GET, JSON): https://pm.healthcaresource.com/JobseekerAPI/Site/{client}/api/v2/JobPostingV2?id={posting id}
  where the posting id is the integer after the underscore in documentId (31484); the same id is the public page
  https://pm.healthcaresource.com/cs/{client}/#/job/31484 (stored as /cs/{client}/?job=31484#/job/31484 so the
  fragment-dropping URL normalization still tells postings apart). userArea.jobSummaryDisplay holds the HTML description,
  userArea.jobSummary the plain text, workHours / salaryRange / requisitionNumber the rest.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .. import http, textutil
from ..models import Listing
from .base import Adapter, AdapterError

HOST = "pm.healthcaresource.com"
CLIENT_RE = re.compile(r"/(?:CS|cs)/([A-Za-z0-9_.-]+)")
API_RE = re.compile(r"/JobseekerSearchAPI/([A-Za-z0-9_.-]+)/api/Search", re.I)


def client_id(hospital: dict) -> str | None:
    cfg = hospital.get("ats_config") or {}
    for key in ("search_api", "client_path", "backend_client_path"):
        v = str(cfg.get(key) or "")
        m = API_RE.search(v) or CLIENT_RE.search(v)
        if m:
            return m.group(1)
    for key in ("careers_search_url", "careers_url"):
        u = hospital.get(key) or ""
        if HOST in u:
            m = CLIENT_RE.search(urlsplit(u).path) or CLIENT_RE.search(u)
            if m:
                return m.group(1)
    return None


def posting_id(document_id: str) -> str | None:
    m = re.search(r"_(\d+)$", str(document_id or ""))
    return m.group(1) if m else None


def org_chain(org: dict) -> list[str]:
    out = []
    while isinstance(org, dict) and org:
        n = org.get("name") or org.get("legalName")
        if n:
            out.append(str(n))
        org = org.get("subOrganization")
    return out


class HealthcareSourceAdapter(Adapter):
    platform = "healthcaresource"
    PAGE = 100
    MAX_HITS = 300

    def _client(self) -> str:
        c = client_id(self.hospital)
        if not c:
            raise AdapterError("healthcaresource: client id unknown (set ats_config.client_path, e.g. /cs/caromont)")
        return c

    def search(self, keywords):
        client = self._client()
        self.method = "api"
        api = f"https://{HOST}/JobseekerSearchAPI/{client}/api/Search?size={self.PAGE}"
        out: dict[str, Listing] = {}
        errors = 0
        for kw in keywords:
            start = 0
            while not self.out_of_time():
                body = {"from": start, "query": {"bool": {"must": {"multi_match": {"query": kw, "fields": ["title", "description"]}}}}}
                try:
                    data = self.post_json(api, body, headers={"Referer": f"https://{HOST}/cs/{client}/", "Origin": f"https://{HOST}"})
                except (http.FetchError, ValueError) as e:
                    errors += 1
                    self.note(f"search '{kw}': {e}")
                    break
                hits = ((data.get("hits") or {}).get("hits")) or []
                total = (data.get("hits") or {}).get("total")
                total = int(total.get("value", 0)) if isinstance(total, dict) else int(total or 0)
                for h in hits:
                    li = self.parse_hit(h.get("_source") or {}, client)
                    if li:
                        out.setdefault(li.norm, li)
                start += self.PAGE
                if not hits or start >= min(total, self.MAX_HITS):
                    break
        if errors == len(keywords):
            raise AdapterError("; ".join(self.notes[-3:]))
        return list(out.values())

    def parse_hit(self, src: dict, client: str) -> Listing | None:
        title = str(src.get("title") or src.get("name") or "").strip()
        pid = posting_id(src.get("documentId"))
        if not title or not pid:
            return None
        addr = (src.get("jobLocation") or {}).get("address") or {}
        loc = addr.get("addressLocalityRegion") or ", ".join(x for x in [addr.get("addressLocality"), addr.get("addressRegion")] if x)
        orgs = org_chain(src.get("hiringOrganization") or {})
        ua = src.get("userArea") or {}
        return Listing(
            # the SPA routes on the fragment; the id is repeated in the query so the board's URL normalization
            # (which drops fragments) keeps one job per posting
            title=title, url=f"https://{HOST}/cs/{client}/?job={pid}#/job/{pid}", location_text=loc or "",
            posted_at=textutil.parse_date(src.get("datePosted")), external_id=str(ua.get("requisitionNumber") or pid),
            extra={"pid": pid, "client": client, "facility": " ".join(orgs[1:2]) if len(orgs) > 1 else "",
                   "orgs": orgs, "department": str(src.get("occupationalCategory") or ""),
                   "employment_type": str(src.get("employmentType") or ""), "work_hours": str(src.get("workHours") or "")},
        )

    def details(self, listing):
        pid, client = listing.extra.get("pid"), listing.extra.get("client") or self._client()
        data = self.get_json(f"https://{HOST}/JobseekerAPI/Site/{client}/api/v2/JobPostingV2?id={pid}")
        ua = data.get("userArea") or {}
        html = ua.get("jobSummaryDisplay") or ""
        desc = textutil.html_to_text(html) if html else str(ua.get("jobSummary") or "").replace("#", " ")
        out = {"description": desc, "external_id": str(ua.get("requisitionNumber") or listing.external_id),
               "posted_at": textutil.parse_date(data.get("datePosted")) or listing.posted_at}
        et = data.get("employmentType") or listing.extra.get("employment_type")
        if et:
            out["employment_type"] = textutil.normalize_employment(str(et)) or str(et)
        if data.get("workHours"):
            out["schedule"] = str(data["workHours"])[:120]
        if ua.get("salaryRange"):
            out["salary_text"] = str(ua["salaryRange"])
        dept = listing.extra.get("department")
        orgs = org_chain(data.get("hiringOrganization") or {}) or listing.extra.get("orgs") or []
        if len(orgs) > 2:
            dept = orgs[-1]
        if dept:
            out["department"] = dept
        if len(orgs) > 1:
            listing.extra["facility"] = orgs[1]
            out["facility"] = orgs[1]
        return out
