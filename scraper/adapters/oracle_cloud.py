"""Oracle Recruiting Cloud (Oracle Fusion HCM Candidate Experience).

Search: GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true
        &expand=requisitionList.secondaryLocations,flexFieldsFacet.values,requisitionList.requisitionFlexFields
        &finder=findReqs;siteNumber={site},facetsList=NONE,limit=25,offset={n},keyword="{kw}",sortBy=POSTING_DATES_DESC
        -> {"items": [{"TotalJobsCount": int, "requisitionList": [{"Id","Title","PostedDate","PrimaryLocation",
                       "ShortDescriptionStr", ...}]}]}
Detail: GET .../recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id="{id}",siteNumber={site}
        -> items[0]: ExternalDescriptionStr, ExternalResponsibilitiesStr, ExternalQualificationsStr, JobSchedule,
           JobShift, ExternalPostedStartDate, Category, Organization ...
Public posting URL: https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{id}
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

from .. import http, textutil
from ..models import Listing
from .base import Adapter, AdapterError

SITE_RE = re.compile(r"/sites/([A-Za-z0-9_]+)")


class OracleCloudAdapter(Adapter):
    platform = "oracle_cloud"
    LIMIT = 25

    def _conf(self):
        host = self.cfg.get("host")
        site = self.cfg.get("site_number") or self.cfg.get("site")
        for u in (self.hospital.get("careers_search_url"), self.hospital.get("careers_url")):
            if not u:
                continue
            p = urlsplit(u)
            if not host and "oraclecloud.com" in p.netloc:
                host = p.netloc
            m = SITE_RE.search(p.path)
            if m and not site:
                site = m.group(1)
        if host and not site:
            # Branded front ends (jobs.example.org/en/sites/CX_1001) proxy the same REST API
            site = "CX_1"
            self.note("site_number not configured; assuming CX_1")
        if not host:
            raise AdapterError("oracle_cloud: host unknown (need ats_config.host like xxxx.fa.us2.oraclecloud.com)")
        host = host.split(":")[0]
        if "oraclecloud.com" not in host:
            # branded front end (e.g. jobs.atlantichealth.org/en/sites/CX_1001): find the real REST host
            page = self.hospital.get("careers_search_url") or self.hospital.get("careers_url") or f"https://{host}/"
            try:
                html = self.get(page).text
                m = re.search(r"([a-z0-9-]+\.fa(?:\.[a-z0-9-]+)?\.oraclecloud\.com)", html)
                if m:
                    self.note(f"branded host {host} -> REST host {m.group(1)}")
                    self._public_host = host
                    host = m.group(1)
            except http.FetchError as e:
                self.note(f"branded host page: {e}")
        return host, site

    def search(self, keywords):
        host, site = self._conf()
        self._host, self._site = host, site
        self.method = "api"
        base = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        out: dict[str, Listing] = {}
        errors = 0
        for kw in keywords:
            offset = 0
            while not self.out_of_time():
                finder = (f"findReqs;siteNumber={site},facetsList=NONE,limit={self.LIMIT},offset={offset},"
                          f"keyword=%22{quote(kw)}%22,sortBy=POSTING_DATES_DESC")
                url = (f"{base}?onlyData=true&expand=requisitionList.secondaryLocations,flexFieldsFacet.values,"
                       f"requisitionList.requisitionFlexFields&finder={finder}")
                try:
                    data = self.get_json(url, headers={"Referer": f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/requisitions"})
                except (http.FetchError, ValueError) as e:
                    errors += 1
                    self.note(f"search '{kw}': {e}")
                    break
                items = data.get("items") or [{}]
                block = items[0] if items else {}
                reqs = block.get("requisitionList") or []
                total = int(block.get("TotalJobsCount") or 0)
                for r in reqs:
                    rid = str(r.get("Id") or "")
                    if not rid:
                        continue
                    pub = getattr(self, "_public_host", None)
                    url_pub = (f"https://{pub}/en/sites/{site}/job/{rid}" if pub else
                               f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{rid}")
                    li = Listing(title=(r.get("Title") or "").strip(), url=url_pub,
                                 location_text=r.get("PrimaryLocation") or "",
                                 posted_at=textutil.parse_date(r.get("PostedDate")), external_id=rid,
                                 snippet=r.get("ShortDescriptionStr") or "", extra={"id": rid})
                    out.setdefault(li.norm, li)
                offset += self.LIMIT
                if not reqs or offset >= min(total, 250):
                    break
        if errors == len(keywords):
            raise AdapterError("; ".join(self.notes[-3:]))
        return list(out.values())

    def details(self, listing):
        rid = listing.extra.get("id") or listing.external_id
        url = (f"https://{self._host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all"
               f"&onlyData=true&finder=ById;Id=%22{rid}%22,siteNumber={self._site}")
        data = self.get_json(url)
        items = data.get("items") or []
        if not items:
            return {"closed": True}
        it = items[0]
        parts = []
        for key, label in (("ExternalDescriptionStr", ""), ("ExternalResponsibilitiesStr", "Responsibilities"),
                           ("ExternalQualificationsStr", "Qualifications"), ("CorporateDescriptionStr", ""),
                           ("OrganizationDescriptionStr", "")):
            v = it.get(key)
            if v:
                parts.append((f"<h3>{label}</h3>" if label else "") + v)
        loc = it.get("PrimaryLocation") or listing.location_text
        city, state = textutil.split_location(loc)
        return {
            "description": textutil.html_to_text("\n".join(parts)),
            "posted_at": textutil.parse_date(it.get("ExternalPostedStartDate")) or listing.posted_at,
            "employment_type": textutil.normalize_employment(it.get("JobSchedule")),
            "shift": it.get("JobShift") or None,
            "department": it.get("Organization") or it.get("Department") or it.get("Category"),
            "location_text": loc,
            "city": city, "state": state,
            "external_id": rid,
        }
