"""Merge run results into data/jobs.json (spec/jobs_schema.md rules).

- new normalized url           -> insert, first_seen_at = last_seen_at = run time, status active
- known url seen again         -> bump last_seen_at, status active (re-open stale), fill empty fields
- known url reported closed    -> status closed
- known url not seen for STALE_DAYS+ while its hospital was checked (reachable) this run -> status stale
- never deletes; never touches jobs whose hospital was unreachable this run (except nothing)
Safety: refuse (raise SafetyError) if the new active count < SAFETY_MIN_RATIO * previous active count.
Accepts both run-file shapes: automated (data/runs/<date>.json, from scraper.run) and the Claude-driven
per-region files (data/jobs_runs/<run_id>_<region>.json with checked/unreachable/jobs/seen_again).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from . import config
from .models import JOB_FIELDS, job_id, normalize_url


class SafetyError(Exception):
    pass


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_jobs(path: Path) -> dict:
    if not path.exists():
        return {"generated_at": None, "last_run": None, "jobs": []}
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError(f"{path} is not a jobs.json document")
    return data


def active_count(doc: dict) -> int:
    return sum(1 for j in doc.get("jobs", []) if j.get("status", "active") == "active")


def _seen_entries(run: dict) -> Iterable[str]:
    for x in run.get("seen_again") or []:
        if isinstance(x, str):
            yield x
        elif isinstance(x, dict) and x.get("url"):
            yield x["url"]


def merge(existing: dict, runs: list[dict], now: datetime | None = None, stale_days: int = config.STALE_DAYS) -> tuple[dict, dict]:
    now = now or datetime.now(timezone.utc)
    now_s = _iso(now)
    jobs = [dict(j) for j in existing.get("jobs", [])]
    by_norm: dict[str, dict] = {}
    for j in jobs:
        if not j.get("url"):
            continue
        n = normalize_url(j["url"])
        j["id"] = j.get("id") or job_id(j["url"])
        by_norm.setdefault(n, j)
    prev_active = active_count(existing)

    stats = {"new": 0, "updated": 0, "stale": 0, "closed": 0, "reopened": 0}
    checked: set[str] = set()
    unreachable: set[str] = set()
    new_jobs: list[dict] = []
    notes = []
    run_ids = []
    for run in runs:
        run_ids.append(str(run.get("run_id") or ""))
        checked.update(run.get("checked") or [])
        unreachable.update(run.get("unreachable") or [])
        ts = now_s
        if run.get("compacted"):
            # compacted run files (scripts/merge_jobs.py --compact) carry only a job index: never insert stubs
            run = dict(run, seen_again=list(run.get("seen_again") or []) + [j.get("url") for j in run.get("jobs") or [] if j.get("url")],
                       jobs=[])
        for rj in run.get("jobs") or []:
            url = rj.get("url")
            if not url:
                continue
            n = normalize_url(url)
            cur = by_norm.get(n)
            if cur is None:
                rec = {k: rj.get(k) for k in JOB_FIELDS}
                rec["id"] = job_id(url)
                rec["first_seen_at"] = ts
                rec["last_seen_at"] = ts
                rec["status"] = "active"
                rec["requirements"] = rec.get("requirements") or []
                rec["tags"] = rec.get("tags") or []
                rec["source"] = rec.get("source") or "employer"
                jobs.append(rec)
                by_norm[n] = rec
                new_jobs.append(rec)
                stats["new"] += 1
            else:
                if cur.get("status") != "active":
                    stats["reopened"] += 1
                cur["last_seen_at"] = ts
                cur["status"] = "active"
                for k in JOB_FIELDS:
                    if k in ("id", "first_seen_at", "last_seen_at", "status"):
                        continue
                    if (cur.get(k) in (None, "", [])) and rj.get(k) not in (None, "", []):
                        cur[k] = rj[k]
                stats["updated"] += 1
        for url in _seen_entries(run):
            cur = by_norm.get(normalize_url(url))
            if cur is not None:
                if cur.get("status") != "active":
                    stats["reopened"] += 1
                cur["last_seen_at"] = ts
                cur["status"] = "active"
                stats["updated"] += 1
        for url in run.get("closed") or []:
            cur = by_norm.get(normalize_url(url if isinstance(url, str) else url.get("url", "")))
            if cur is not None and cur.get("status") != "closed":
                cur["status"] = "closed"
                stats["closed"] += 1
        if run.get("notes"):
            notes.append(str(run["notes"]))

    reachable = checked - unreachable
    cutoff = now - timedelta(days=stale_days)
    for j in jobs:
        if j.get("status") != "active":
            continue
        if j.get("hospital_slug") not in reachable:
            continue
        last = _parse_ts(j.get("last_seen_at"))
        if last is not None and last < cutoff:
            j["status"] = "stale"
            stats["stale"] += 1

    # active first, then relevance desc, newest first within each (two stable sorts)
    jobs.sort(key=lambda j: (j.get("first_seen_at") or "", j.get("posted_at") or ""), reverse=True)
    jobs.sort(key=lambda j: (j.get("status") != "active", -(int(j.get("relevance") or 0))))
    new_doc = {
        "generated_at": now_s,
        "last_run": {
            "run_id": ",".join(r for r in run_ids if r) or now_s[:10],
            "started_at": min((r.get("started_at") for r in runs if r.get("started_at")), default=now_s),
            "finished_at": max((r.get("finished_at") for r in runs if r.get("finished_at")), default=now_s),
            "hospitals_checked": len(reachable),
            "hospitals_unreachable": sorted(unreachable),
            "new_jobs": stats["new"],
            "updated_jobs": stats["updated"],
            "stale_jobs": stats["stale"],
            "notes": "; ".join(notes)[:2000] or f"automated scrape: {stats['new']} new, {stats['closed']} closed, {stats['reopened']} reopened",
        },
        "jobs": jobs,
    }
    new_active = active_count(new_doc)
    stats["prev_active"], stats["active"] = prev_active, new_active
    if prev_active and new_active < config.SAFETY_MIN_RATIO * prev_active:
        raise SafetyError(f"active jobs would drop from {prev_active} to {new_active} "
                          f"(< {int(config.SAFETY_MIN_RATIO * 100)}%); refusing to write")
    stats["new_jobs"] = new_jobs
    return new_doc, stats


def write_jobs(path: Path, doc: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    json.loads(tmp.read_text())  # must parse
    tmp.replace(path)
