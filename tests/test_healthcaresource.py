"""HealthcareSource (Symplr) adapter against responses captured on Actions (probes 39650100056 and 39653988170, CaroMont)."""
from __future__ import annotations

import json
from pathlib import Path

from scraper import detect
from scraper.adapters import get_adapter
from scraper.adapters.healthcaresource import HealthcareSourceAdapter, client_id, posting_id

FIX = Path(__file__).parent / "fixtures"
CAROMONT = {"slug": "caromont-regional-gastonia", "name": "CaroMont Regional Medical Center", "careers_platform": "custom",
            "careers_search_url": "https://pm.healthcaresource.com/cs/caromont#/results?what=nurse%20practitioner",
            "ats_config": {"host": "pm.healthcaresource.com", "client_path": "/cs/caromont",
                           "search_api": "https://pm.healthcaresource.com/JobseekerSearchAPI/caromont/api/Search"}}
EMERSON = {"slug": "emerson-hospital-concord", "careers_search_url": "https://emersonhealth.org/careers/careers-search/",
           "ats_config": {"host": "emersonhealth.org", "backend_host": "pm.healthcaresource.com", "backend_client_path": "/CS/emerson"}}


def test_detection_and_client():
    assert detect.detect_platform(CAROMONT) == "healthcaresource"
    assert detect.detect_platform(EMERSON) == "healthcaresource"
    assert client_id(CAROMONT) == "caromont" and client_id(EMERSON) == "emerson"
    assert get_adapter("healthcaresource") is HealthcareSourceAdapter
    assert detect.board_key(CAROMONT, "healthcaresource") != detect.board_key(EMERSON, "healthcaresource")
    assert posting_id("8023_31484") == "31484" and posting_id("") is None


def test_search_hits_parse():
    a = HealthcareSourceAdapter(CAROMONT)
    data = json.loads((FIX / "healthcaresource_search.json").read_text())
    rows = [a.parse_hit(h["_source"], "caromont") for h in data["hits"]["hits"]]
    rows = [r for r in rows if r]
    assert len(rows) == 3
    r = rows[0]
    assert r.title == "Nurse Practitioner/Physician Assistant"
    assert r.url == "https://pm.healthcaresource.com/cs/caromont/#/job/31484"
    assert r.location_text == "Gastonia, NC" and r.posted_at == "2026-01-26"
    assert r.extra["facility"] == "CaroMont Medical Group"


def test_details(monkeypatch):
    a = HealthcareSourceAdapter(CAROMONT)
    data = json.loads((FIX / "healthcaresource_search.json").read_text())
    li = a.parse_hit(data["hits"]["hits"][0]["_source"], "caromont")
    detail = json.loads((FIX / "healthcaresource_detail.json").read_text())
    seen = {}
    monkeypatch.setattr(a, "get_json", lambda url, **kw: seen.setdefault("url", url) and detail or detail)
    d = a.details(li)
    assert "JobPostingV2?id=31484" in seen["url"]
    assert "Advanced Practice Providers" in d["description"] and "<p" not in d["description"]
    assert d["employment_type"] == "Full time" and d["schedule"].startswith("Mon-Fri")
    assert d["facility"] == "CaroMont Medical Group" and d["external_id"] == "40206"
