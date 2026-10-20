"""Scrape orchestration: ledger -> board groups -> adapters -> listings -> triage -> details -> Job records."""
from __future__ import annotations

import concurrent.futures as cf
import re
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from . import attribution, browser, config, detect, http, relevance, textutil
from .adapters import get_adapter
from .adapters.base import Adapter, AdapterError
from .adapters.generic import GenericAdapter
from .models import Job, Listing, normalize_url


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class BoardResult:
    key: str
    platform: str
    hospitals: list[dict]
    adapter_used: str = ""
    method: str = ""
    listings: list[Listing] = field(default_factory=list)
    error: str | None = None
    error_status: int | None = None
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0
    requests: int = 0
    jobs: list[Job] = field(default_factory=list)
    seen_urls: list[str] = field(default_factory=list)
    closed_urls: list[str] = field(default_factory=list)
    per_hospital: dict[str, dict[str, Any]] = field(default_factory=dict)
    skipped: str | None = None
    details_fetched: int = 0
    details_failed: int = 0
    details_deferred: int = 0


# --------------------------------------------------------------------------------------------- sniffing

SNIFF_RULES = [
    ("jibe", re.compile(r"window\._jibe|jibecdn\.com|ng-app=\"jibeapply\"")),
    ("talentbrew", re.compile(r"tbcdn\.talentbrew\.com")),
    ("careersite", re.compile(r"window\.csns|CareerSite\.Path\.configure")),
    ("phenom", re.compile(r"cdn\.phenompeople\.com|phApp\.ddo")),
    ("symphony", re.compile(r"var cws_opts\s*=|jobsapi-google\.m-cloud\.io")),
    ("successfactors", re.compile(r"rmkcdn\.successfactors\.com|jobTitle-link")),
]
WD_LINK = re.compile(r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)")
OC_LINK = re.compile(r"https?://([a-z0-9-]+\.fa\.[a-z0-9]+\.oraclecloud\.com)(?::443)?/hcmUI/CandidateExperience/[a-z]{2}/sites/([A-Za-z0-9_]+)")


def sniff_adapter(hospital: dict, deadline: float, notes: list[str]) -> Adapter | None:
    """For generic/custom hospitals: fetch the careers page and recognize a known front end."""
    url = hospital.get("careers_search_url") or hospital.get("careers_url")
    if not url:
        return None
    html, final = None, url
    try:
        r = http.get(url)
        html, final = r.text, r.url
    except http.FetchError as e:
        notes.append(f"sniff: {e}")
        if http.is_bot_block(e):
            # Cloudflare-fronted CareerSite tenants expose /search/jobs.json to a real browser
            host = urlsplit(url).netloc
            try:
                res = browser.render(f"https://{host}/search/jobs.json?q=nurse+practitioner", settle_ms=500)
                if '"total_entries"' in (res.get("html") or ""):
                    notes.append("sniff: Cloudflare-protected Jobvite/Talemetry CareerSite (jobs.json)")
                    return _make("careersite", hospital, deadline, host=host)
                res = browser.render(url)
                if res.get("status", 200) < 400:
                    html, final = res["html"], res["url"]
            except Exception as e2:  # noqa: BLE001
                notes.append(f"sniff playwright: {type(e2).__name__}: {str(e2)[:120]}")
    except Exception as e:  # noqa: BLE001
        notes.append(f"sniff: {type(e).__name__}: {str(e)[:160]}")
    if not html:
        return None
    host = urlsplit(final).netloc
    for name, pat in SNIFF_RULES:
        if pat.search(html):
            notes.append(f"sniff: recognized {name} front end on {host}")
            return _make(name, hospital, deadline, host=host, page_url=final)
    m = OC_LINK.search(html)
    if m:
        notes.append(f"sniff: page links to Oracle Recruiting Cloud {m.group(1)} site {m.group(2)}")
        h2 = dict(hospital, ats_config={**(hospital.get("ats_config") or {}), "host": m.group(1), "site_number": m.group(2)})
        return get_adapter("oracle_cloud")(h2, deadline)
    m = WD_LINK.search(html)
    if m and m.group(3).lower() not in ("login", "job", "details"):
        notes.append(f"sniff: page links to Workday {m.group(1)}.{m.group(2)} site {m.group(3)}")
        h2 = dict(hospital, ats_config={**(hospital.get("ats_config") or {}),
                                        "host": f"{m.group(1)}.{m.group(2)}.myworkdayjobs.com",
                                        "tenant": m.group(1), "site": m.group(3)})
        return get_adapter("workday")(h2, deadline)
    return None


def _make(name: str, hospital: dict, deadline: float, host: str, page_url: str | None = None) -> Adapter:
    cfg = dict(hospital.get("ats_config") or {})
    cfg["host"] = host
    h2 = dict(hospital, ats_config=cfg)
    if name == "phenom" and page_url:
        h2["careers_search_url"] = page_url
    cls = get_adapter(name)
    if name in ("jibe", "talentbrew"):
        return cls(h2, deadline, host=host)
    return cls(h2, deadline)


# --------------------------------------------------------------------------------------------- board run

def relevance_context(hospital: dict, level: str, group: list[dict]) -> dict:
    """Hospital dict as seen by relevance.classify, adjusted for how confidently we attributed the posting."""
    ctx = dict(hospital)
    childrens_board = all(h.get("hospital_type") in relevance.CHILDRENS_TYPES for h in group) and \
        ("child" in (hospital.get("health_system") or hospital.get("name") or "").lower())
    if hospital.get("hospital_type") in relevance.CHILDRENS_TYPES and not (level == "name" or childrens_board):
        ctx["hospital_type"] = "academic_medical_center"
    if level == "none" and len(group) >= 1 and not childrens_board:
        ctx["hotspot"] = False  # never keep a non-pediatric role we could not place at a ledger hospital
    return ctx


def run_board(key: str, platform: str, group: list[dict], known: dict[str, dict], opts, global_deadline: float) -> BoardResult:
    t0 = time.time()
    res = BoardResult(key=key, platform=platform, hospitals=group)
    if time.time() > global_deadline:
        res.skipped = "run budget exhausted before this board started"
        return res
    deadline = min(global_deadline, t0 + config.HOSPITAL_BUDGET_SECONDS)
    lead = group[0]
    adapter: Adapter | None = None
    try:
        if platform == "generic":
            adapter = sniff_adapter(lead, deadline, res.notes)
            if adapter is not None:
                res.platform = f"generic->{adapter.platform}"
        if adapter is None:
            adapter = get_adapter(platform)(lead, deadline)
        res.adapter_used = adapter.platform
        listings = adapter.search(opts.keywords)
        res.method = adapter.method
        res.notes.extend(adapter.notes)
        if not listings and platform != "generic" and adapter.platform != "generic":
            # fall back to the generic adapter before declaring the board empty
            g = GenericAdapter(lead, deadline, reason=f"{adapter.platform} adapter returned 0 listings; trying generic")
            gl = g.search(opts.keywords)
            if gl:
                adapter, listings = g, gl
                res.adapter_used, res.method = "generic", g.method
            res.notes.extend(g.notes)
        res.listings = listings
    except Exception as e:  # noqa: BLE001
        res.error = f"{type(e).__name__}: {str(e)[:500]}"
        res.error_status = getattr(e, "status", None)
        if adapter is not None:
            res.notes.extend(adapter.notes)
            res.requests = adapter.requests
        if not isinstance(e, (AdapterError, http.FetchError)):
            res.notes.append("traceback: " + traceback.format_exc(limit=4)[-800:])
        # last resort: generic adapter
        if platform != "generic" and time.time() < deadline:
            try:
                g = GenericAdapter(lead, deadline, reason=f"{platform} failed: {res.error[:160]}")
                gl = g.search(opts.keywords)
                res.notes.extend(g.notes)
                if gl:
                    adapter, res.listings = g, gl
                    res.adapter_used, res.method = "generic", g.method
                    res.notes.append(f"recovered via generic adapter after: {res.error}")
                    res.error = None
            except Exception as e2:  # noqa: BLE001
                res.notes.append(f"generic fallback failed: {type(e2).__name__}: {str(e2)[:200]}")
    res.seconds = round(time.time() - t0, 1)
    for h in group:
        res.per_hospital[h["slug"]] = {"listings": 0, "candidates": 0, "new": 0, "known": 0, "samples": []}
    if res.error or adapter is None:
        res.requests = adapter.requests if adapter else 0
        return res

    # ---- attribute + triage
    triaged: list[tuple[int, Listing, dict, str]] = []
    dropped_out_of_area = 0
    for li in res.listings:
        city, st = textutil.split_location(li.location_text)
        if st and st not in attribution.FOOTPRINT:
            dropped_out_of_area += 1
            continue
        ped_title = relevance.peds_signal(li.title, "", li.location_text)[0] >= 2
        hosp, level = attribution.attribute(group, li.location_text, li.extra.get("facility", ""), ped_title)
        ctx = relevance_context(hosp, level, group)
        pr = relevance.listing_priority(li.title, li.location_text, ctx)
        res.per_hospital[hosp["slug"]]["listings"] += 1
        if pr == 0:
            continue
        if li.snippet and pr < 3 and relevance.peds_signal("", li.snippet)[0] >= 1:
            pr = 3
        triaged.append((pr, li, hosp, level))
    if dropped_out_of_area:
        res.notes.append(f"dropped {dropped_out_of_area} listings outside ME-SC footprint")

    # known jobs: seen only. Unknown (or known with empty description): details
    triaged.sort(key=lambda x: -x[0])
    budget_left = opts.max_details
    for pr, li, hosp, level in triaged:
        k = known.get(li.norm)
        if k and (k.get("description") or opts.no_details) and k.get("status") != "closed":
            res.seen_urls.append(li.url)
            slug = k.get("hospital_slug") or hosp["slug"]
            ph = res.per_hospital.setdefault(slug, {"listings": 0, "candidates": 0, "new": 0, "known": 0, "samples": []})
            ph["known"] = ph.get("known", 0) + 1
            if len(ph["samples"]) < 3:
                ph["samples"].append({"title": k.get("title"), "url": k.get("url"), "posted_at": k.get("posted_at"),
                                      "relevance": k.get("relevance"), "setting": k.get("setting"), "known": True})
            continue
        if pr < 2 and not opts.no_details and budget_left <= opts.max_details // 3:
            res.details_deferred += 1
            continue
        detail: dict = {}
        if not opts.no_details:
            if budget_left <= 0 or time.time() > deadline:
                res.details_deferred += 1
                continue
            budget_left -= 1
            try:
                detail = adapter.details(li) or {}
                res.details_fetched += 1
            except Exception as e:  # noqa: BLE001
                res.details_failed += 1
                st = getattr(e, "status", None)
                if st in (404, 410):
                    detail = {"closed": True}
                else:
                    res.notes.append(f"detail failed {li.url}: {type(e).__name__}: {str(e)[:160]}")
                    detail = {"description": textutil.clean_text(li.snippet or "")}
        if detail.get("closed"):
            if k:
                res.closed_urls.append(li.url)
            continue
        job = build_job(li, detail, group, hosp, level, adapter, res.method)
        if job is None:
            continue
        res.jobs.append(job)
        ph = res.per_hospital.setdefault(job.hospital_slug, {"listings": 0, "candidates": 0, "new": 0, "known": 0, "samples": []})
        ph["candidates"] += 1
        ph["new"] += 0 if k else 1
        if len(ph["samples"]) < 3:
            ph["samples"].append({"title": job.title, "url": job.url, "posted_at": job.posted_at,
                                  "relevance": job.relevance, "setting": job.setting})
    for slug, ph in res.per_hospital.items():
        ph["candidates"] += ph.get("known", 0)
    res.requests = adapter.requests
    res.seconds = round(time.time() - t0, 1)
    if res.details_deferred:
        res.notes.append(f"{res.details_deferred} low-priority listings deferred (detail budget/time); they are picked up on later runs")
    return res


def build_job(li: Listing, d: dict, group: list[dict], hosp: dict, level: str, adapter: Adapter, method: str) -> Job | None:
    desc = d.get("description") or textutil.clean_text(li.snippet or "")
    location = d.get("location_text") or li.location_text or ""
    # re-attribute with the richer detail location when available
    if len(group) > 1 and d.get("location_text"):
        ped_title = relevance.peds_signal(li.title, "", location)[0] >= 2
        h2, l2 = attribution.attribute(group, f"{location} {li.location_text}", li.extra.get("facility", "") + " " + (d.get("department") or ""), ped_title)
        if l2 != "none":
            hosp, level = h2, l2
    city, st = d.get("city"), d.get("state")
    if not (city and st):
        c2, s2 = textutil.split_location(location)
        city, st = city or c2, st or s2
    if st and st not in attribution.FOOTPRINT:
        return None
    ctx = relevance_context(hosp, level, group)
    v = relevance.classify(li.title, desc, ctx, location, d.get("department") or "")
    if not v.keep or v.relevance <= 0:
        return None
    reason = v.reason
    if level == "none" and len(group) >= 1 and (hosp.get("health_system") or "") and \
            not all(h.get("hospital_type") in relevance.CHILDRENS_TYPES for h in group):
        reason += f" Posted on the {hosp.get('health_system')} system board without a matching location; attributed to the flagship {hosp.get('name')}."
    tags = v.tags
    title = li.title or d.get("title") or ""
    job = Job(
        hospital_slug=hosp["slug"], title=title, url=d.get("url") or li.url,
        apply_url=(d.get("apply_url") if d.get("apply_url") and normalize_url(d.get("apply_url")) != normalize_url(li.url) else None),
        source="employer", external_id=d.get("external_id") or li.external_id, location_text=location,
        city=city, state=st, department=d.get("department") or li.extra.get("department") or None,
        setting=v.setting, employment_type=d.get("employment_type") or textutil.normalize_employment(
            "per diem" if re.search(r"per[\s-]*diem|\bPRN\b", title, re.I) else
            ("part time" if re.search(r"part[\s-]*time", title, re.I) else None)),
        shift=d.get("shift") or textutil.guess_shift(title), schedule=d.get("schedule") or textutil.guess_schedule(title, desc[:1500]),
        salary_text=d.get("salary_text") or textutil.find_salary(desc), posted_at=d.get("posted_at") or li.posted_at,
        summary=textutil.summarize(title, hosp.get("name", ""), relevance.SETTING_LABELS.get(v.setting, ""), desc, tags),
        description=desc, requirements=relevance.extract_requirements(desc), relevance=v.relevance,
        relevance_reason=reason, tags=tags, status="active",
        raw_notes=f"scraper {adapter.platform}/{method}" + (f"; attribution={level}" if len(group) > 1 else ""),
    )
    if job.employment_type in ("Unavailable", ""):
        job.employment_type = None
    return job


# --------------------------------------------------------------------------------------------- run

def group_boards(hospitals: list[dict]) -> list[tuple[str, str, list[dict]]]:
    groups: dict[str, tuple[str, list[dict]]] = {}
    for h in hospitals:
        p = detect.detect_platform(h)
        k = detect.board_key(h, p)
        groups.setdefault(k, (p, []))[1].append(h)
    return [(k, p, g) for k, (p, g) in groups.items()]


def run(hospitals: list[dict], known: dict[str, dict], opts) -> dict:
    started = now_iso()
    t0 = time.time()
    global_deadline = t0 + opts.budget
    boards = group_boards(hospitals)
    results: list[BoardResult] = []
    # Not a `with` block: its __exit__ waits for every thread, which would let one hung board defeat the
    # hard budget. Daemon-ish behaviour: abandoned threads keep running until process exit.
    ex = cf.ThreadPoolExecutor(max_workers=config.WORKERS, thread_name_prefix="board")
    # most expensive boards first (system boards with many hospitals, browser-only platforms)
    boards.sort(key=lambda b: (-(b[1] in ("careersite", "symphony", "generic")), -len(b[2])))
    futs = {ex.submit(run_board, k, p, g, known, opts, global_deadline): (k, p, g) for k, p, g in boards}
    hard_timeout = max(60.0, global_deadline + config.HARD_GRACE_SECONDS - time.time())
    try:
        for fut in cf.as_completed(futs, timeout=hard_timeout):
            k, p, g = futs[fut]
            try:
                results.append(fut.result())
            except Exception as e:  # noqa: BLE001
                br = BoardResult(key=k, platform=p, hospitals=g, error=f"{type(e).__name__}: {e}")
                for h in g:
                    br.per_hospital[h["slug"]] = {"listings": 0, "candidates": 0, "new": 0, "known": 0, "samples": []}
                results.append(br)
    except cf.TimeoutError:
        done_keys = {r.key for r in results}
        for f, (k, p, g) in futs.items():
            if k not in done_keys:
                f.cancel()
                br = BoardResult(key=k, platform=p, hospitals=g, error="hard run budget exceeded; board abandoned")
                for h in g:
                    br.per_hospital[h["slug"]] = {"listings": 0, "candidates": 0, "new": 0, "known": 0, "samples": []}
                results.append(br)
    ex.shutdown(wait=False, cancel_futures=True)
    try:
        browser.shutdown()
    except Exception:  # noqa: BLE001
        pass
    return assemble(results, started, opts)


def assemble(results: list[BoardResult], started: str, opts) -> dict:
    hospitals_status = []
    checked, unreachable = [], []
    jobs: list[dict] = []
    seen: list[str] = []
    closed: list[str] = []
    seen_ids = set()
    for r in sorted(results, key=lambda r: r.key):
        for h in r.hospitals:
            ph = r.per_hospital.get(h["slug"], {})
            if r.skipped:
                status, err = "skipped", r.skipped
            elif r.error:
                status, err = "error", r.error
            elif not r.listings and r.adapter_used != "generic":
                status, err = "zero_results", "board reachable but returned 0 listings for every keyword (check search URL / adapter)"
            else:
                status, err = "ok", None
            entry = {
                "slug": h["slug"], "name": h.get("name"), "platform": r.platform, "adapter": r.adapter_used,
                "method": r.method, "board": r.key, "board_hospitals": len(r.hospitals),
                "board_listings": len(r.listings), "listings": ph.get("listings", 0),
                "candidates": ph.get("candidates", 0), "new": ph.get("new", 0), "known": ph.get("known", 0),
                "status": status, "error": err, "http_status": r.error_status, "seconds": r.seconds,
                "requests": r.requests, "details_fetched": r.details_fetched, "details_failed": r.details_failed,
                "notes": r.notes[:12], "samples": ph.get("samples", []),
                "sample_listings": [{"title": li.title, "url": li.url, "posted_at": li.posted_at,
                                     "location": li.location_text} for li in r.listings[:3]],
            }
            hospitals_status.append(entry)
            (checked if status == "ok" else unreachable).append(h["slug"])
        for j in r.jobs:
            if j.id in seen_ids:
                continue
            seen_ids.add(j.id)
            jobs.append(j.to_dict())
        seen.extend(r.seen_urls)
        closed.extend(r.closed_urls)
    finished = now_iso()
    return {
        "run_id": opts.run_id, "mode": "automated", "started_at": started, "finished_at": finished,
        "dry_run": bool(opts.dry_run), "no_details": bool(opts.no_details),
        "keywords": opts.keywords, "checked": checked, "unreachable": unreachable,
        "unreachable_detail": {e["slug"]: e["error"] for e in hospitals_status if e["status"] != "ok"},
        "hospitals": hospitals_status, "jobs": jobs, "seen_again": sorted(set(seen)), "closed": sorted(set(closed)),
        "totals": {"hospitals": len(hospitals_status), "checked": len(checked), "unreachable": len(unreachable),
                   "listings": sum(len(r.listings) for r in results), "candidates": len(jobs) + len(set(seen)),
                   "new_candidates": len(jobs), "seconds": round(sum(r.seconds for r in results), 1)},
    }
