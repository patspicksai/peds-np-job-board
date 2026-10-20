"""Workday Candidate Experience (myworkdayjobs.com) JSON API.

Search: POST https://{host}/wday/cxs/{tenant}/{site}/jobs
        {"appliedFacets": {}, "limit": 20, "offset": N, "searchText": kw}
        -> {"total": int, "jobPostings": [{"title", "externalPath", "locationsText", "postedOn", "bulletFields"}]}
Detail: GET  https://{host}/wday/cxs/{tenant}/{site}{externalPath}
        -> {"jobPostingInfo": {"jobDescription" (HTML), "jobReqId", "startDate", "location", "timeType",
                               "externalUrl", ...}, "hiringOrganization": {...}}
Public posting URL: https://{host}/{site}{externalPath}  (externalUrl in detail when present)
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .. import http, textutil
from ..models import Listing
from .base import Adapter, AdapterError

WD_HOST_RE = re.compile(r"^([a-z0-9-]+)\.(wd\d+)\.(myworkdayjobs|myworkdaysite)\.com$", re.I)
LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def parse_workday_url(url: str) -> dict:
    """https://aah.wd5.myworkdayjobs.com/en-US/External/details/... -> host, tenant, site."""
    if not url:
        return {}
    p = urlsplit(url)
    m = WD_HOST_RE.match(p.netloc)
    if not m:
        return {}
    segs = [s for s in p.path.split("/") if s]
    if segs and segs[0] == "wday" and len(segs) >= 4 and segs[1] == "cxs":
        return {"host": p.netloc, "tenant": segs[2], "site": segs[3]}
    if segs and LOCALE_RE.match(segs[0]):
        segs = segs[1:]
    # myworkdaysite.com uses /recruiting/{tenant}/{site}
    if segs and segs[0] == "recruiting" and len(segs) >= 3:
        return {"host": p.netloc, "tenant": segs[1], "site": segs[2]}
    site = segs[0] if segs and segs[0] not in ("job", "details", "login") else None
    return {k: v for k, v in {"host": p.netloc, "tenant": m.group(1), "site": site}.items() if v}


class WorkdayAdapter(Adapter):
    platform = "workday"
    PAGE = 20

    def _conf(self) -> tuple[str, str, str]:
        cfg = dict(self.cfg)
        for u in (self.hospital.get("careers_search_url"), self.hospital.get("careers_url")):
            parsed = parse_workday_url(u or "")
            for k, v in parsed.items():
                cfg.setdefault(k, v)
        host, tenant, site = cfg.get("host"), cfg.get("tenant"), cfg.get("site")
        if host and not tenant:
            m = WD_HOST_RE.match(host)
            tenant = m.group(1) if m else None
        if not (host and tenant):
            raise AdapterError("workday: cannot derive host/tenant from ats_config or careers URLs")
        if not site:
            site = self._discover_site(host, tenant)
        return host, tenant, site

    def _discover_site(self, host: str, tenant: str) -> str:
        """Try common site names against the jobs endpoint (422/404/500 = wrong site)."""
        guesses = [tenant, tenant.capitalize(), tenant.upper(), "External", "Careers", f"{tenant}_Careers",
                   f"{tenant}careers", "en-US"]
        for g in guesses:
            try:
                data = self.post_json(f"https://{host}/wday/cxs/{tenant}/{g}/jobs",
                                      {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
                                      ok=(200,))
                if isinstance(data, dict) and "jobPostings" in data:
                    self.note(f"discovered workday site '{g}'")
                    return g
            except Exception:  # noqa: BLE001
                continue
        raise AdapterError(f"workday: site name unknown for tenant {tenant} (tried {', '.join(guesses)})")

    def search(self, keywords):
        host, tenant, site = self._conf()
        self.method = "api"
        self._base = f"https://{host}/wday/cxs/{tenant}/{site}"
        self._public = f"https://{host}/{site}"
        self._referer = f"https://{host}/{site}"
        out: dict[str, Listing] = {}
        errors = 0
        for kw in keywords:
            offset, total = 0, None
            while not self.out_of_time():
                try:
                    data = self.post_json(f"{self._base}/jobs",
                                          {"appliedFacets": {}, "limit": self.PAGE, "offset": offset, "searchText": kw},
                                          headers={"Origin": f"https://{host}", "Referer": self._referer})
                except http.FetchError as e:
                    errors += 1
                    self.note(f"search '{kw}': {e}")
                    break
                posts = data.get("jobPostings") or []
                if total is None:
                    total = int(data.get("total") or 0)
                for p in posts:
                    path = p.get("externalPath") or ""
                    if not path:
                        continue
                    url = f"{self._public}{path}"
                    bullets = p.get("bulletFields") or []
                    li = Listing(title=(p.get("title") or "").strip(), url=url,
                                 location_text=p.get("locationsText") or "",
                                 posted_at=textutil.parse_date(p.get("postedOn")),
                                 external_id=bullets[0] if bullets else None,
                                 extra={"path": path})
                    out.setdefault(li.norm, li)
                offset += self.PAGE
                # Workday caps deep paging; 10 pages x 20 = 200 per keyword is plenty for NP searches
                if not posts or offset >= min(total or 0, 200):
                    break
        if errors == len(keywords):
            raise AdapterError("; ".join(self.notes[-3:]))
        return list(out.values())

    def details(self, listing):
        path = listing.extra.get("path") or urlsplit(listing.url).path.split(f"/{self._public.rsplit('/', 1)[-1]}", 1)[-1]
        data = self.get_json(f"{self._base}{path}", headers={"Referer": listing.url})
        info = data.get("jobPostingInfo") or {}
        if not info:
            return {"closed": True}
        desc = textutil.html_to_text(info.get("jobDescription") or "")
        out = {
            "description": desc,
            "external_id": info.get("jobReqId") or listing.external_id,
            "posted_at": textutil.parse_date(info.get("startDate")) or listing.posted_at,
            "employment_type": textutil.normalize_employment(info.get("timeType")),
            "location_text": info.get("location") or listing.location_text,
        }
        ext = info.get("externalUrl")
        if ext:
            out["url"] = ext
        if info.get("canApply") is False or info.get("posted") is False:
            out["closed"] = True
        org = (data.get("hiringOrganization") or {}).get("name")
        if org:
            out["department"] = re.sub(r"^\d+\s+", "", org).strip() or None
        return out
