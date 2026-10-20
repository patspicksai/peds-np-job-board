from datetime import datetime, timedelta, timezone

import pytest

from scraper import merge as m
from scraper.models import job_id

NOW = datetime(2026, 10, 20, 10, 15, tzinfo=timezone.utc)


def job(url, slug="duke-childrens-durham", **kw):
    d = {"url": url, "hospital_slug": slug, "title": "NP - PICU", "relevance": 3, "description": "x"}
    d.update(kw)
    return d


def run(jobs=(), checked=("duke-childrens-durham",), unreachable=(), seen=(), closed=()):
    return {"run_id": "2026-10-20", "checked": list(checked), "unreachable": list(unreachable),
            "jobs": list(jobs), "seen_again": list(seen), "closed": list(closed)}


def test_insert_new_sets_ids_and_timestamps():
    doc, st = m.merge({"jobs": []}, [run([job("https://careers.duke.edu/job/a/1/")])], now=NOW)
    j = doc["jobs"][0]
    assert st["new"] == 1
    assert j["id"] == job_id("https://careers.duke.edu/job/a/1/")
    assert j["first_seen_at"] == j["last_seen_at"] == "2026-10-20T10:15:00Z"
    assert j["status"] == "active"


def test_dedupe_by_normalized_url_and_bump_last_seen():
    old = {"jobs": [dict(job("https://careers.duke.edu/job/a/1/"), id=job_id("https://careers.duke.edu/job/a/1/"),
                         first_seen_at="2026-10-01T10:00:00Z", last_seen_at="2026-10-01T10:00:00Z", status="active")]}
    doc, st = m.merge(old, [run([job("https://careers.duke.edu/job/a/1?utm_source=x")])], now=NOW)
    assert len(doc["jobs"]) == 1 and st["new"] == 0 and st["updated"] == 1
    assert doc["jobs"][0]["first_seen_at"] == "2026-10-01T10:00:00Z"
    assert doc["jobs"][0]["last_seen_at"] == "2026-10-20T10:15:00Z"


def test_seen_again_bumps():
    old = {"jobs": [dict(job("https://x.org/j/1"), first_seen_at="2026-10-18T00:00:00Z", last_seen_at="2026-10-18T00:00:00Z", status="active")]}
    doc, st = m.merge(old, [run(seen=["https://x.org/j/1/"])], now=NOW)
    assert doc["jobs"][0]["last_seen_at"] == "2026-10-20T10:15:00Z"


def test_stale_only_when_hospital_reachable():
    old_ts = (NOW - timedelta(days=8)).isoformat().replace("+00:00", "Z")
    old = {"jobs": [
        dict(job("https://x.org/j/1", slug="a"), first_seen_at=old_ts, last_seen_at=old_ts, status="active"),
        dict(job("https://x.org/j/2", slug="b"), first_seen_at=old_ts, last_seen_at=old_ts, status="active"),
        dict(job("https://x.org/j/3", slug="a"), first_seen_at=old_ts, last_seen_at=(NOW - timedelta(days=2)).isoformat(), status="active"),
    ]}
    doc, st = m.merge(old, [run(checked=["a", "b"], unreachable=["b"])], now=NOW)
    by = {j["url"]: j for j in doc["jobs"]}
    assert by["https://x.org/j/1"]["status"] == "stale"
    assert by["https://x.org/j/2"]["status"] == "active"   # hospital b unreachable: no stale
    assert by["https://x.org/j/3"]["status"] == "active"   # seen 2 days ago
    assert st["stale"] == 1


def test_closed_and_never_deleted():
    old = {"jobs": [dict(job("https://x.org/j/1"), first_seen_at="2026-10-18T00:00:00Z", last_seen_at="2026-10-18T00:00:00Z", status="active"),
                    dict(job("https://x.org/j/2"), first_seen_at="2026-10-18T00:00:00Z", last_seen_at="2026-10-19T00:00:00Z", status="active")]}
    doc, st = m.merge(old, [run(closed=["https://x.org/j/1"], seen=["https://x.org/j/2"])], now=NOW)
    assert len(doc["jobs"]) == 2
    assert {j["url"]: j["status"] for j in doc["jobs"]}["https://x.org/j/1"] == "closed"


def test_stale_job_reopens_when_seen():
    old = {"jobs": [dict(job("https://x.org/j/1"), first_seen_at="2026-09-01T00:00:00Z", last_seen_at="2026-09-10T00:00:00Z", status="stale")]}
    doc, st = m.merge(old, [run(seen=["https://x.org/j/1"])], now=NOW)
    assert doc["jobs"][0]["status"] == "active" and st["reopened"] == 1


def test_safety_refuses_big_drop():
    old_ts = (NOW - timedelta(days=9)).isoformat()
    old = {"jobs": [dict(job(f"https://x.org/j/{i}"), first_seen_at=old_ts, last_seen_at=old_ts, status="active") for i in range(10)]}
    with pytest.raises(m.SafetyError):
        m.merge(old, [run()], now=NOW)


def test_fills_empty_fields_on_known():
    old = {"jobs": [dict(job("https://x.org/j/1", description=""), first_seen_at="2026-10-18T00:00:00Z", last_seen_at="2026-10-18T00:00:00Z", status="active")]}
    doc, _ = m.merge(old, [run([job("https://x.org/j/1", description="Full text")])], now=NOW)
    assert doc["jobs"][0]["description"] == "Full text"


def test_last_run_block():
    doc, _ = m.merge({"jobs": []}, [run([job("https://x.org/j/1")], checked=["duke-childrens-durham", "x"], unreachable=["x"])], now=NOW)
    lr = doc["last_run"]
    assert lr["new_jobs"] == 1 and lr["hospitals_unreachable"] == ["x"] and lr["hospitals_checked"] == 1


def test_merge_cli_roundtrip(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    runf = tmp_path / "r.json"
    runf.write_text(json.dumps(run([job("https://x.org/j/1")])))
    jobs = tmp_path / "jobs.json"
    out = subprocess.run([sys.executable, str(root / "scripts" / "merge_jobs.py"), str(runf), "--jobs", str(jobs)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    doc = json.loads(jobs.read_text())
    assert len(doc["jobs"]) == 1
    # idempotent
    subprocess.run([sys.executable, str(root / "scripts" / "merge_jobs.py"), str(runf), "--jobs", str(jobs)], check=True)
    assert len(json.loads(jobs.read_text())["jobs"]) == 1
    # corrupt jobs.json -> refuse
    jobs.write_text("{not json")
    bad = subprocess.run([sys.executable, str(root / "scripts" / "merge_jobs.py"), str(runf), "--jobs", str(jobs)])
    assert bad.returncode == 4
