"""Infor CloudSuite adapter against responses captured on Actions (probe 39428696372, Northern Light Health)."""
from __future__ import annotations

import json
from pathlib import Path

from scraper.adapters import get_adapter
from scraper.adapters.infor import InforAdapter, location_from_code, posting_date
from scraper import detect

FIX = Path(__file__).parent / "fixtures"
NLH = {"slug": "northern-light-emmc-bangor", "name": "Northern Light Eastern Maine Medical Center",
       "careers_platform": "custom",
       "careers_url": "https://css-emh-prd.inforcloudsuite.com/hcm/Jobs/page/JobsHomePage?csk.showusingxi=true&csk.JobBoard=EXTERNAL&csk.HROrganization=10",
       "ats_config": {"host": "css-emh-prd.inforcloudsuite.com", "job_board": "EXTERNAL", "hr_organization": "10"}}


def test_detect_routes_infor_hosts():
    assert detect.detect_platform(NLH) == "infor"
    kh = {"careers_url": "https://www.kaleidahealth.org/careers/",
          "ats_config": {"host": "css-y9x4ku9mqsygapwx-prd.inforcloudsuite.com", "job_board": "KH-EXTERNAL", "hr_organization": "1000"}}
    assert detect.detect_platform(kh) == "infor"
    assert get_adapter("infor") is InforAdapter


def test_helpers():
    assert location_from_code("US:ME:Portland") == "Portland, ME"
    assert location_from_code("US:NY:Buffalo:Main") == "Buffalo, NY"
    assert posting_date("20261019-00000000") == "2026-10-19"
    assert posting_date("") is None


def test_list_url_and_rows():
    a = InforAdapter(NLH)
    url = a.list_url()
    assert url.startswith("https://css-emh-prd.inforcloudsuite.com/hcm/Jobs/list/JobPosting.JobSearchList?")
    assert "relation=JobBoard%2810%2CEXTERNAL%29.Postings" in url and "csk.HROrganization=10" in url
    data = json.loads((FIX / "infor_list.json").read_text())
    rows = a.parse_rows(data)
    assert len(rows) == 4
    np = [r for r in rows if "Nurse Practitioner" in r.title]
    assert np, [r.title for r in rows]
    r = np[0]
    assert r.location_text.endswith(", ME") and r.external_id and r.posted_at
    assert "JobPostingDisplay" in r.url and "csk.JobBoard=EXTERNAL" in r.url
    assert r.extra["detail_url"].count("pageop=load") == 1


def test_details_from_captured_json(monkeypatch):
    a = InforAdapter(NLH)
    data = json.loads((FIX / "infor_list.json").read_text())
    li = a.parse_rows(data)[0]
    detail = json.loads((FIX / "infor_detail.json").read_text())
    monkeypatch.setattr(a, "get_json", lambda url, **kw: detail)
    d = a.details(li)
    assert "Northern Light" in d["description"] and "<p>" not in d["description"]
    assert d["facility"] == "Northern Light A.R. Gould Hospital"
    assert li.extra["facility"] == d["facility"]
