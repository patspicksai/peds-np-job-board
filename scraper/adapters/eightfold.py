"""Eightfold AI career sites ({tenant}.eightfold.ai/careers or careers.example.org powered by Eightfold).

Search: GET https://{host}/api/apply/v2/jobs?domain={domain}&query={kw}&start={n}&num=10&sort_by=relevance
        -> {"positions": [{"id","name","location","locations","t_create","t_update","department",
            "canonicalPositionUrl","ats_job_id","job_description"?}], "count": int}
Detail: GET https://{host}/api/apply/v2/jobs/{id}?domain={domain} -> {"job_description": HTML, ...}
Public posting URL: canonicalPositionUrl or https://{host}/careers/job/{id}
"""
from __future__ import annotations

from urllib.parse import quote, urlsplit, parse_qs

from .. import http, textutil
from ..models import Listing
from .base import Adapter, AdapterError


class EightfoldAdapter(Adapter):
    platform = "eightfold"
    NUM = 10

    def _conf(self):
        host = self.cfg.get("host") or urlsplit(self.search_url).netloc
        domain = self.cfg.get("domain")
        if not domain:
            q = parse_qs(urlsplit(self.search_url).query)
            domain = (q.get("domain") or [None])[0]
        if not domain:
            site = self.hospital.get("website") or ""
            domain = urlsplit(site).netloc.replace("www.", "") or host.split(".", 1)[-1]
        return host, domain

    def search(self, keywords):
        host, domain = self._conf()
        self._host, self._domain = host, domain
        self.method = "api"
        out: dict[str, Listing] = {}
        errors = 0
        for kw in keywords:
            start = 0
            while not self.out_of_time():
                url = (f"https://{host}/api/apply/v2/jobs?domain={quote(domain)}&query={quote(kw)}"
                       f"&start={start}&num={self.NUM}&sort_by=relevance")
                try:
                    data = self.get_json(url, headers={"Referer": f"https://{host}/careers"})
                except (http.FetchError, ValueError) as e:
                    errors += 1
                    self.note(f"search '{kw}': {e}")
                    break
                pos = data.get("positions") or []
                total = int(data.get("count") or 0)
                for p in pos:
                    pid = p.get("id")
                    if not pid:
                        continue
                    url_pub = p.get("canonicalPositionUrl") or f"https://{host}/careers/job/{pid}"
                    li = Listing(title=(p.get("name") or "").strip(), url=url_pub,
                                 location_text=p.get("location") or ", ".join(p.get("locations") or []),
                                 posted_at=textutil.parse_date(p.get("t_create")),
                                 external_id=str(p.get("ats_job_id") or p.get("display_job_id") or pid),
                                 extra={"id": pid, "pos": p})
                    out.setdefault(li.norm, li)
                start += self.NUM
                if not pos or start >= min(total, 200):
                    break
        if errors == len(keywords):
            raise AdapterError("; ".join(self.notes[-3:]))
        return list(out.values())

    def details(self, listing):
        pid = listing.extra.get("id")
        data = self.get_json(f"https://{self._host}/api/apply/v2/jobs/{pid}?domain={quote(self._domain)}")
        p = listing.extra.get("pos") or {}
        return {"description": textutil.html_to_text(data.get("job_description") or ""),
                "department": data.get("department") or p.get("department"),
                "employment_type": textutil.normalize_employment(data.get("type") or data.get("work_type")),
                "posted_at": textutil.parse_date(data.get("t_create")) or listing.posted_at}
