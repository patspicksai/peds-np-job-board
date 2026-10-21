#!/usr/bin/env python3
"""Re-attribute and re-score every job in data/jobs.json with the current scraper rules.

Use after a change to scraper/relevance.py or scraper/attribution.py so postings already on the board get the same
treatment as new ones. Prints every change; writes only with --apply.

Usage: python3 scripts/rescore_jobs.py [--apply] [--jobs data/jobs.json] [--ledger data/hospitals.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scraper import attribution, pipeline, relevance  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--jobs", default=str(ROOT / "data" / "jobs.json"))
    ap.add_argument("--ledger", default=str(ROOT / "data" / "hospitals.json"))
    a = ap.parse_args()

    ledger = json.load(open(a.ledger))["hospitals"]
    attribution.LEDGER = list(ledger)
    by_slug = {h["slug"]: h for h in ledger}
    group_of: dict[str, list[dict]] = {}
    for _key, _platform, group in pipeline.group_boards([h for h in ledger if h.get("active") is not False]):
        for h in group:
            group_of[h["slug"]] = group

    doc = json.load(open(a.jobs))
    changes = Counter()
    lines = []
    for j in doc["jobs"]:
        h = by_slug.get(j["hospital_slug"])
        if not h:
            continue
        group = group_of.get(h["slug"], [h])
        text_loc = j.get("location_text") or ""
        dept = j.get("department") or ""
        ped_title = relevance.peds_signal(j["title"], "", text_loc)[0] >= 2
        diffs, upd = [], {}
        # 1. a system-board posting that names another member hospital of the same system (same state) moves there
        hosp, level = attribution.attribute(group, text_loc, "", ped_title)
        if level == "ledger" and hosp["slug"] != j["hospital_slug"]:
            board_sys = next((x.get("health_system") for x in group if x.get("health_system")), None) or group[0].get("name")
            diffs.append(f"hospital {j['hospital_slug']} -> {hosp['slug']}")
            upd.update(hospital_slug=hosp["slug"], city=hosp.get("city") or j.get("city"), state=hosp.get("state") or j.get("state"),
                       relevance_reason=(j.get("relevance_reason") or "").split(" Posted on the ")[0] +
                       f" Posted on the {board_sys} board; the posting's location names {hosp.get('name')}.")
        else:
            hosp = h
        # 2. re-score, but only to apply caps the current rules add: a Physician Assistant title or an NNP-only
        #    NICU never counts as strong; a non-pediatric role at a children's hospital within a hospital, matched
        #    only through the parent campus name, is not pediatric. Tags are only ever added.
        ctx = pipeline.relevance_context(hosp, "name", group, f"{text_loc} {dept} {j['title']}")
        v = relevance.classify(j["title"], j.get("description") or "", ctx, text_loc, dept)
        old_rel = int(j.get("relevance") or 1)
        capped = v.reason.startswith(("Physician Assistant title", "NICU role that asks for a neonatal nurse practitioner",
                                      "NP role not clearly pediatric"))
        if v.keep and v.relevance < old_rel and capped:
            diffs.append(f"relevance {old_rel} -> {v.relevance}")
            upd.update(relevance=v.relevance, relevance_reason=v.reason + (" " + upd["relevance_reason"].split(" Posted on the ", 1)[1] if "relevance_reason" in upd and " Posted on the " in upd["relevance_reason"] else ""))
            if v.setting != j.get("setting") and j.get("setting") in ("peds_specialty_outpatient", "peds_specialty_inpatient", "peds_inpatient", "mixed") and v.setting == "unspecified":
                diffs.append(f"setting {j.get('setting')} -> {v.setting}")
                upd["setting"] = v.setting
        elif not v.keep and old_rel >= 2 and "not an advanced practice role" not in v.reason and \
                len(j.get("description") or "") >= 300 and "details not fetched" not in (j.get("tags") or []):
            # only when the classifier saw the full posting; thin records keep their earlier (hand-checked) score
            diffs.append(f"relevance {old_rel} -> 1 ({v.reason[:60]})")
            upd.update(relevance=1, relevance_reason=f"Kept for context: {v.reason}")
        add_tags = [t for t in ("PA title", "NNP required") if t in v.tags and t not in (j.get("tags") or [])]
        if add_tags:
            diffs.append(f"tags + {add_tags}")
            upd["tags"] = sorted(set((j.get("tags") or []) + add_tags))
        if diffs:
            lines.append(f"- {j['title'][:70]} @ {j['hospital_slug']}: " + "; ".join(diffs))
            for d in diffs:
                changes[d.split(" ")[0]] += 1
            if a.apply:
                j.update(upd)
    print("\n".join(lines))
    print(f"\n{len(lines)} jobs would change: {dict(changes)}")
    if a.apply and lines:
        json.dump(doc, open(a.jobs, "w"), indent=1, ensure_ascii=False)
        open(a.jobs, "a").write("\n")
        print(f"written {a.jobs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
