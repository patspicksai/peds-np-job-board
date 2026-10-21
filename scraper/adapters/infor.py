"""Infor CloudSuite HCM (GHR / Lawson) external job boards.

Used by Northern Light Health (css-emh-prd.inforcloudsuite.com, board EXTERNAL, org 10), Kaleida Health
(css-y9x4ku9mqsygapwx-prd.inforcloudsuite.com, board KH-EXTERNAL, org 1000) and Catholic Health Buffalo
(chsblawsonprd-lm01.cloud.infor.com:1444/lmghr/Jobs, board EXTERNAL, org CHS).

ats_config: host, job_board, hr_organization, optional base_path (default "/hcm/Jobs"; Lawson tenants use "/lmghr/Jobs").

List (plain GET, JSON; verified on Actions probe 39428696372, 792 rows for Northern Light):
  https://{host}{base}/list/JobPosting.JobSearchList?sortOrderName=JobPosting.ByPostDateBeginSet&pageop=load
      &pagesize=100&dependentList=true&relation=JobBoard({org}%2C{board}).Postings&isAscending=false
      &csk.JobBoard={board}&csk.HROrganization={org}
  -> {"dataViewSet": {"data": [{"resourceId": "JobPosting[JobPostingSet](10,96997,1)",
        "fields": {"Description": {"value": title}, "LocationOfJobDescriptionForSort": {"value": "US:ME:Portland"},
                   "JobRequisition": {"value": 96997}, "PostingDateRange": {"value": "20261019-00000000"},
                   "WorkType": {"value": "PRN"}, "Category": {"stateValues": [{"label": "Nursing Support"}]},
                   "_op_EMHDepartmentDerived_spc_translation_cp_": {"value": "Emergency Department"}, ...}}, ...],
       "pagingInfo": {"hasNext": true, ...}, "pagingUrls": {"nextPageUrl": ...}, "recordCount": 792}}
  There is no keyword parameter, so every page is read (100 rows a page) and titles are filtered here.
Detail (plain GET, JSON): the row's resourceId as JobPosting%5BJobPostingSet%5D%28{org}%2C{req}%2C{n}%29.JobPostingDisplay
  with pageop=load -> fields._op_PositionDescription_spc_translation_cp_ (HTML description) and
  fields._op_<Tenant>JobRequisitionMemberOrgLocationCategoryWorkType_spc_translation_cp_
  ("Northern Light A.R. Gould Hospital |US:ME:Presque Isle | Nursing- RN | PRN", the facility for attribution).
"""
from __future__ import annotations

import re
from urllib.parse import quote

from .. import http, relevance, textutil
from ..models import Listing
from .base import Adapter, AdapterError

RESOURCE_RE = re.compile(r"JobPosting\[JobPostingSet\]\(([^,()]+),(\d+),(\d+)\)")
MAX_PAGES = 12


def _field(fields: dict, key: str):
    v = fields.get(key)
    if isinstance(v, dict):
        return v.get("value")
    return v


def _label(fields: dict, key: str) -> str:
    v = fields.get(key)
    if isinstance(v, dict):
        sv = v.get("stateValues") or []
        if sv and isinstance(sv[0], dict) and sv[0].get("label"):
            return str(sv[0]["label"])
        return str(v.get("value") or "")
    return str(v or "")


def _first_value(fields: dict, *needles: str) -> str:
    """Value of the first field whose key contains all the needles (tenant-prefixed derived fields)."""
    for k, v in fields.items():
        if all(n.lower() in k.lower() for n in needles):
            val = v.get("value") if isinstance(v, dict) else v
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def location_from_code(code: str) -> str:
    """'US:ME:Portland' -> 'Portland, ME'; 'US:NY:Buffalo:Main' -> 'Buffalo, NY'."""
    parts = [p.strip() for p in (code or "").split(":") if p.strip()]
    if len(parts) >= 3 and parts[0].upper() == "US":
        return f"{parts[2]}, {parts[1]}"
    if len(parts) == 2 and parts[0].upper() == "US":
        return parts[1]
    return ", ".join(parts)


def posting_date(value: str) -> str | None:
    m = re.match(r"^(\d{4})(\d{2})(\d{2})", str(value or ""))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


class InforAdapter(Adapter):
    platform = "infor"
    PAGE = 100

    def _base(self) -> tuple[str, str, str, str]:
        host = self.cfg.get("host") or self.host
        board = self.cfg.get("job_board")
        org = self.cfg.get("hr_organization")
        base = (self.cfg.get("base_path") or "/hcm/Jobs").rstrip("/")
        if not (host and board and org):
            # fall back to the csk parameters of the careers URL
            m_b = re.search(r"csk\.JobBoard=([^&]+)", self.search_url or "")
            m_o = re.search(r"csk\.HROrganization=([^&]+)", self.search_url or "")
            board = board or (m_b.group(1) if m_b else None)
            org = org or (m_o.group(1) if m_o else None)
        if not (host and board and org):
            raise AdapterError("infor: need ats_config.host, job_board and hr_organization")
        return host, base, str(board), str(org)

    def list_url(self) -> str:
        host, base, board, org = self._base()
        rel = quote(f"JobBoard({org},{board}).Postings", safe="")
        return (f"https://{host}{base}/list/JobPosting.JobSearchList?sortOrderName=JobPosting.ByPostDateBeginSet"
                f"&pageop=load&pagesize={self.PAGE}&dependentList=true&relation={rel}&isAscending=false"
                f"&csk.JobBoard={quote(board, safe='')}&csk.HROrganization={quote(org, safe='')}")

    def posting_urls(self, resource_id: str) -> tuple[str, str] | None:
        """(public posting URL, JSON detail URL) for a row's resourceId."""
        m = RESOURCE_RE.search(resource_id or "")
        if not m:
            return None
        host, base, board, org = self._base()
        key = f"{m.group(1)},{m.group(2)},{m.group(3)}"
        enc = quote(f"JobPosting[JobPostingSet]({key})", safe="")
        tail = f"&csk.JobBoard={quote(board, safe='')}&csk.HROrganization={quote(org, safe='')}"
        public = f"https://{host}{base}/form/{enc}.JobPostingDisplay?pagesize=1&navigation={enc}.JobPostingDisplayNav{tail}"
        detail = (f"https://{host}{base}/form/{enc}.JobPostingDisplay?pageop=load&pagesize=1&navigation={enc}.JobPostingDisplayNav"
                  f"&fromlist=JobPosting.SearchForJobsResults{tail}")
        return public, detail

    def parse_rows(self, data: dict) -> list[Listing]:
        out = []
        for row in ((data.get("dataViewSet") or {}).get("data") or []):
            fields = row.get("fields") or {}
            title = str(_field(fields, "Description") or "").strip()
            if not title:
                continue
            urls = self.posting_urls(str(row.get("resourceId") or ""))
            if not urls:
                continue
            public, detail = urls
            loc_code = str(_field(fields, "LocationOfJobDescriptionForSort") or _field(fields, "LocationOfJob") or "")
            req = _field(fields, "JobRequisition") or _field(fields, "JobId")
            dept = _first_value(fields, "Department")
            li = Listing(title=title, url=public, location_text=location_from_code(loc_code),
                         posted_at=posting_date(_field(fields, "PostingDateRange") or _field(fields, "PostingDateRange_prd_Begin")),
                         external_id=str(req) if req is not None else None,
                         extra={"detail_url": detail, "department": dept, "category": _label(fields, "Category"),
                                "work_type": str(_field(fields, "WorkType") or ""),
                                "schedule": _label(fields, "JobRequisition_col_WorkSchedule")})
            out.append(li)
        return out

    def search(self, keywords):
        self.method = "api"
        url = self.list_url()
        out: dict[str, Listing] = {}
        seen_pages = 0
        total = None
        while url and seen_pages < MAX_PAGES and not self.out_of_time():
            try:
                data = self.get_json(url, headers={"Referer": self.search_url or f"https://{self._base()[0]}/"})
            except (http.FetchError, ValueError) as e:
                if seen_pages == 0:
                    raise AdapterError(f"infor: list request failed: {e}") from e
                self.note(f"page {seen_pages + 1}: {e}")
                break
            seen_pages += 1
            dv = data.get("dataViewSet") or {}
            if total is None:
                total = dv.get("recordCount")
            rows = self.parse_rows(data)
            for li in rows:
                if relevance.title_is_app_candidate(li.title):
                    out.setdefault(li.norm, li)
            paging = dv.get("pagingInfo") or {}
            next_url = (dv.get("pagingUrls") or {}).get("nextPageUrl")
            if not rows or not paging.get("hasNext") or not next_url:
                break
            url = next_url
        self.note(f"infor: {seen_pages} page(s), {total} postings on the board, {len(out)} advanced practice titles")
        return list(out.values())

    def details(self, listing):
        durl = listing.extra.get("detail_url") or listing.url
        data = self.get_json(durl)
        fields = data.get("fields") or {}
        html = _first_value(fields, "PositionDescription") or _first_value(fields, "Description", "translation")
        desc = textutil.html_to_text(html) if html else ""
        facility = _first_value(fields, "MemberOrg")
        if facility:
            facility = facility.split("|")[0].strip()
        out = {"description": desc, "external_id": listing.external_id, "posted_at": listing.posted_at}
        if listing.extra.get("department"):
            out["department"] = listing.extra["department"]
        if facility:
            out["facility"] = facility
            listing.extra["facility"] = facility
        wt = (listing.extra.get("work_type") or "").strip()
        if wt:
            out["employment_type"] = textutil.normalize_employment(wt) or wt
        return out
