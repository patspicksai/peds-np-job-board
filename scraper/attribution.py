"""Attribute a listing from a shared (system-wide) board to one ledger hospital.

spec/jobs_schema.md: "If a posting is at a system level and could be any of several hospitals, pick the one
named in the posting's location; if none, pick the system's flagship and say so in relevance_reason."

match levels: "name" (a distinctive word of the hospital / parent name or an ats_config.location_aliases
entry appears in the location / facility text), "city" (the hospital's city appears), "none" (flagship fallback).
Postings in states outside the board's footprint (ME..SC + DC) are dropped by the caller.
"""
from __future__ import annotations

import re

from .textutil import STATES

FOOTPRINT = {"ME", "NH", "VT", "MA", "RI", "CT", "NY", "NJ", "PA", "DE", "MD", "DC", "VA", "NC", "SC"}
STOP = {
    "hospital", "hospitals", "medical", "center", "centre", "health", "healthcare", "system", "systems", "children",
    "childrens", "children's", "university", "regional", "memorial", "community", "general", "the", "of", "at",
    "and", "saint", "st", "campus", "main", "clinic", "clinics", "care", "network", "inc", "llc", "for", "us",
    "north", "south", "east", "west", "new", "city", "county", "valley", "center's", "med", "ctr", "hosp",
}
STATE_WORDS = set()
for _name in STATES:
    STATE_WORDS.update(_name.split())


def _norm(s: str) -> str:
    s = (s or "").lower().replace("’", "'")
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def distinctive_tokens(hospital: dict) -> set[str]:
    system_words = set(_norm(hospital.get("health_system") or "").split())
    toks: set[str] = set()
    for field in ("name", "parent_hospital"):
        for w in _norm(hospital.get(field) or "").split():
            w2 = w.replace("'s", "").strip("'")
            if len(w2) < 3 or w2 in STOP or w2 in STATE_WORDS or w2 in system_words or w2.isdigit():
                continue
            toks.add(w2)
    return toks


def aliases(hospital: dict) -> list[str]:
    cfg = hospital.get("ats_config") or {}
    out = [_norm(a) for a in (cfg.get("location_aliases") or []) if a]
    for field in ("name", "parent_hospital"):
        v = _norm(hospital.get(field) or "")
        if v:
            out.append(v)
    return out


def match_level(hospital: dict, text: str) -> int:
    t = " " + _norm(text) + " "
    for a in aliases(hospital):
        if a and f" {a} " in t:
            return 3
    words = set(t.split())
    toks = distinctive_tokens(hospital)
    if toks and toks & {w.replace("'s", "") for w in words}:
        return 2
    city = _norm(hospital.get("city") or "")
    if city and f" {city} " in t:
        return 1
    return 0


def flagship(group: list[dict]) -> dict:
    for h in group:
        if (h.get("ats_config") or {}).get("flagship"):
            return h
    rank = {"childrens_hospital": 0, "childrens_hospital_within_hospital": 1, "academic_medical_center": 2}
    return sorted(group, key=lambda h: rank.get(h.get("hospital_type"), 5))[0]


def attribute(group: list[dict], location_text: str, extra_text: str = "", pediatric_title: bool = False):
    """-> (hospital, level) where level is 'name' | 'city' | 'none'."""
    text = f"{location_text} {extra_text}"
    scored = [(match_level(h, text), h) for h in group]
    best = max(s for s, _ in scored) if scored else 0
    if best == 0:
        return flagship(group), "none"
    cands = [h for s, h in scored if s == best]
    if len(cands) > 1:
        pref = ("childrens_hospital", "childrens_hospital_within_hospital") if pediatric_title else \
            ("academic_medical_center", "regional_hospital", "community_hospital")
        # rank by position in the preference tuple (academic before regional before community, or children's first)
        cands.sort(key=lambda h: pref.index(h.get("hospital_type")) if h.get("hospital_type") in pref else 9)
    return cands[0], ("name" if best >= 2 else "city")
