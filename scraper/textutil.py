"""HTML -> clean plain text, JSON-LD JobPosting extraction, date parsing, location parsing."""
from __future__ import annotations

import html as htmllib
import json
import re
from datetime import date, datetime, timedelta, timezone

from bs4 import BeautifulSoup, NavigableString, Tag

from . import config

BLOCK_TAGS = {"p", "div", "section", "article", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
              "ul", "ol", "table", "tr", "blockquote", "pre", "dl", "dt", "dd", "form", "fieldset", "main",
              "aside", "figure", "hr"}
DROP_TAGS = {"script", "style", "noscript", "svg", "nav", "iframe", "form", "button", "select", "input",
             "template", "head", "link", "meta", "canvas", "object", "embed"}
BOILER_RE = re.compile(
    r"^(?:apply(?: now)?|share(?: this job)?|save(?: job)?|back to (?:search|results)|print|email|tweet|"
    r"skip to (?:main )?content|sign in|log ?in|create (?:an )?account|cookie|accept|view all jobs|"
    r"similar jobs|recently viewed|job alerts?|search jobs|returning (?:user|candidate)|"
    r"share on (?:facebook|twitter|linkedin|x)\w*(?: ?share to \w+)?|share to (?:facebook|twitter|linkedin)|share with email|share:?|"
    r"follow us.*|read more|show more|show less|internal apply|saved|save job|loading job|loading\.\.\.|"
    r"join our talent network|liquid error: internal|apply now\s*>?|mail|\u00a0)$", re.I)
# Paragraphs that start a tail of non-job boilerplate (only cut when they appear in the second half)
TAIL_MARKERS = re.compile(
    r"^(?:hiring scam alert|notice regarding maine employer surveillance|keywords:|"
    r"similar jobs|recently viewed jobs|job alerts|sign up for job alerts|connect with us|"
    r"need help finding the right job\?|explore (?:nearby|similar) jobs)", re.I)
STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
ABBRS = set(STATES.values())


# ------------------------------------------------------------------ text

def _walk(node, out: list[str]) -> None:
    for child in node.children:
        if isinstance(child, NavigableString):
            if child.__class__.__name__ in ("Comment", "CData", "ProcessingInstruction", "Doctype"):
                continue
            out.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name.lower()
        if name in DROP_TAGS:
            continue
        if name == "br":
            out.append("\n")
            continue
        if name == "li":
            out.append("\n• ")
            _walk(child, out)
            out.append("\n")
            continue
        if name in ("td", "th"):
            _walk(child, out)
            out.append("  ")
            continue
        if name in BLOCK_TAGS:
            out.append("\n\n")
            _walk(child, out)
            out.append("\n\n")
            continue
        _walk(child, out)


def html_to_text(fragment: str, max_chars: int | None = config.DESCRIPTION_MAX) -> str:
    """HTML (or already-escaped HTML) to plain text with paragraph breaks preserved."""
    if not fragment:
        return ""
    s = fragment
    # Workday and some ATSs double-escape: &amp;#xa; or &lt;p&gt;
    for _ in range(2):
        if re.search(r"&lt;\s*/?\s*(?:p|br|div|li|ul|strong|b|span)\b", s) or "&amp;#" in s:
            s = htmllib.unescape(s)
    if "<" in s and ">" in s:
        soup = BeautifulSoup(s, "lxml")
        out: list[str] = []
        _walk(soup, out)
        text = "".join(out)
    else:
        text = htmllib.unescape(s)
    return clean_text(text, max_chars)


def clean_text(text: str, max_chars: int | None = config.DESCRIPTION_MAX) -> str:
    text = htmllib.unescape(text or "")
    text = text.replace("\r", "\n").replace(" ", " ").replace("​", "").replace("﻿", "")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    lines = [ln.strip() for ln in text.split("\n")]
    cleaned: list[str] = []
    for ln in lines:
        if ln in ("•", "-", "*"):
            continue
        if ln and BOILER_RE.match(ln):
            continue
        cleaned.append(ln)
    text = "\n".join(cleaned)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"(• [^\n]*)\n\n(?=• )", r"\1\n", text)  # tighten bullet lists
    text = text.strip()
    if max_chars and len(text) > max_chars:
        cut = text[:max_chars]
        sp = cut.rfind(" ")
        if sp > max_chars * 0.8:
            cut = cut[:sp]
        text = cut.rstrip() + "..."
    return text


def trim_boilerplate_tail(text: str) -> str:
    """Cut a trailing block of site boilerplate (scam alerts, SEO keyword lists, similar jobs)."""
    if not text:
        return text
    paras = text.split("\n\n")
    total = len(text)
    pos = 0
    for i, p in enumerate(paras):
        if i > 0 and pos > total * 0.5 and TAIL_MARKERS.match(p.strip()):
            return "\n\n".join(paras[:i]).rstrip()
        pos += len(p) + 2
    return text


def page_main_text(html: str, max_chars: int | None = config.DESCRIPTION_MAX) -> str:
    """Best-effort job description from a full HTML page when there is no JSON-LD."""
    soup = BeautifulSoup(html or "", "lxml")
    for t in soup(list(DROP_TAGS) + ["header", "footer", "aside"]):
        t.decompose()
    selectors = [
        "[itemprop=description]", ".jobdescription", ".job-description", "#job-description", ".jobDescription",
        ".job-details", ".job__description", ".ats-description", ".iCIMS_JobContent", ".iCIMS_InfoMsg_Job",
        "[data-automation-id=jobPostingDescription]", ".job-detail-description", ".description", "#content",
        "article", "main", "[role=main]",
    ]
    for sel in selectors:
        try:
            nodes = soup.select(sel)
        except Exception:  # noqa: BLE001
            continue
        best = max(nodes, key=lambda n: len(n.get_text(" ", strip=True)), default=None)
        if best is not None and len(best.get_text(" ", strip=True)) > 300:
            return html_to_text(str(best), max_chars)
    body = soup.body or soup
    return html_to_text(str(body), max_chars)


# ------------------------------------------------------------------ JSON-LD

def jsonld_objects(html: str) -> list[dict]:
    out: list[dict] = []
    for m in re.finditer(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", html or "", re.S | re.I):
        raw = m.group(1).strip()
        raw = re.sub(r"^\s*<!--|-->\s*$", "", raw)
        for candidate in (raw, re.sub(r"[\x00-\x1f]", " ", raw)):
            try:
                data = json.loads(candidate)
                break
            except Exception:  # noqa: BLE001
                data = None
        if data is None:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if isinstance(d, dict):
                out.append(d)
                g = d.get("@graph")
                if isinstance(g, list):
                    stack.extend(g)
            elif isinstance(d, list):
                stack.extend(d)
    return out


def jsonld_jobposting(html: str) -> dict | None:
    for d in jsonld_objects(html):
        t = d.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(str(x).lower() == "jobposting" for x in types):
            return d
    return None


def jobposting_fields(jp: dict) -> dict:
    """Normalize a schema.org JobPosting into Job-ish fields."""
    out: dict = {}
    if not jp:
        return out
    out["title"] = clean_text(str(jp.get("title") or ""), None) or None
    desc = jp.get("description") or ""
    if isinstance(desc, str) and desc:
        text = html_to_text(desc)
        # drop a bare leading "Description" / "Job Description" heading
        text = re.sub(r"^(?:job\s+)?description:?\s*\n+", "", text, flags=re.I).strip()
        out["description"] = trim_boilerplate_tail(text)
    out["posted_at"] = parse_date(jp.get("datePosted"))
    ident = jp.get("identifier")
    if isinstance(ident, dict):
        out["external_id"] = str(ident.get("value") or ident.get("name") or "") or None
    elif isinstance(ident, (str, int)):
        out["external_id"] = str(ident)
    et = jp.get("employmentType")
    if isinstance(et, list):
        et = ", ".join(str(x) for x in et)
    if et:
        out["employment_type"] = normalize_employment(str(et))
    loc = jp.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if isinstance(loc, dict):
        addr = loc.get("address") or {}
        if isinstance(addr, dict):
            city = addr.get("addressLocality")
            region = addr.get("addressRegion")
            out["city"] = city or None
            out["state"] = state_abbr(region)
            parts = [p for p in [addr.get("streetAddress"), city, region] if p]
            out["location_text"] = ", ".join(str(p) for p in parts)
    sal = jp.get("baseSalary")
    if isinstance(sal, dict):
        val = sal.get("value") or {}
        if isinstance(val, dict):
            lo, hi, unit = val.get("minValue"), val.get("maxValue"), val.get("unitText")
            v = val.get("value")
            if lo or hi or v:
                rng = f"${lo:,}" if isinstance(lo, (int, float)) else (str(lo) if lo else "")
                if hi:
                    rng += f" - ${hi:,}" if isinstance(hi, (int, float)) else f" - {hi}"
                if not rng and v:
                    rng = str(v)
                out["salary_text"] = f"{rng} {('per ' + str(unit).lower()) if unit else ''}".strip()
    org = jp.get("hiringOrganization")
    if isinstance(org, dict):
        out["hiring_org"] = org.get("name")
    return {k: v for k, v in out.items() if v}


# ------------------------------------------------------------------ dates

_REL_RE = re.compile(r"(?:posted\s+)?(today|yesterday|(\d+)\+?\s+(day|days|hour|hours|week|weeks|month|months)\s+ago)", re.I)


def parse_date(value, today: date | None = None) -> str | None:
    """ISO date (YYYY-MM-DD) from ISO strings, epoch ms, 'Oct 19, 2026', 'Posted 3 Days Ago', etc.

    Relative values with a '+' (e.g. "30+ Days Ago") are lower bounds, so they return None (do not invent).
    """
    if value is None or value == "":
        return None
    today = today or datetime.now(timezone.utc).date()
    if isinstance(value, (int, float)):
        try:
            ts = value / 1000 if value > 10**11 else value
            return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
        except Exception:  # noqa: BLE001
            return None
    s = str(value).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    m = re.search(r"/Date\((\d+)", s)
    if m:
        return parse_date(int(m.group(1)), today)
    rel = _REL_RE.search(s)
    if rel:
        if "+" in s:
            return None
        word = rel.group(1).lower()
        if word == "today":
            return today.isoformat()
        if word == "yesterday":
            return (today - timedelta(days=1)).isoformat()
        n, unit = int(rel.group(2)), rel.group(3).lower()
        if unit.startswith("hour"):
            return today.isoformat()
        if unit.startswith("day"):
            return (today - timedelta(days=n)).isoformat()
        if unit.startswith("week"):
            return (today - timedelta(days=7 * n)).isoformat()
        return None
    try:
        from dateutil import parser as dparser
        cleaned = re.sub(r"^(?:posted|date posted|posting date|date)\s*:?\s*", "", s, flags=re.I)
        cleaned = re.sub(r"\bUTC\b", "", cleaned)
        dt = dparser.parse(cleaned, fuzzy=True, default=datetime(today.year, 1, 1))
        if dt.year < 2000 or dt.date() > today + timedelta(days=2):
            return None
        return dt.date().isoformat()
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ misc normalizers

def state_abbr(value) -> str | None:
    if not value:
        return None
    s = str(value).strip()
    if s.upper() in ABBRS and len(s) == 2:
        return s.upper()
    return STATES.get(s.lower())


def split_location(text: str) -> tuple[str | None, str | None]:
    """'Durham, NC, US, 27710' -> ('Durham', 'NC'); 'US-MD-Baltimore' -> ('Baltimore', 'MD')."""
    if not text:
        return None, None
    t = text.strip()
    m = re.match(r"^US-([A-Z]{2})-(.+)$", t)
    if m:
        return m.group(2).strip().title(), m.group(1)
    parts = [p.strip() for p in re.split(r",|\|", t) if p.strip()]
    for i, p in enumerate(parts):
        st = state_abbr(re.sub(r"\s+\d{5}(?:-\d{4})?$", "", p))
        if st and i > 0:
            city = parts[i - 1]
            city = re.sub(r"^\d+\s+.*\s{2,}", "", city)
            return city, st
    m = re.search(r"([A-Za-z .'-]+),?\s+([A-Z]{2})\b(?:\s+\d{5})?", t)
    if m and m.group(2) in ABBRS:
        return m.group(1).strip(), m.group(2)
    return None, None


def normalize_employment(s: str | None) -> str | None:
    if not s:
        return None
    t = s.lower()
    if re.search(r"per[\s_-]*diem|prn|casual|on[\s_-]*call|temporary", t):
        return "Per diem"
    if re.search(r"part", t):
        return "Part time"
    if re.search(r"full", t):
        return "Full time"
    return s.strip().title()[:40]


SHIFT_RE = re.compile(r"\b(day(?:s)?|night(?:s)?|evening(?:s)?|rotating|variable|weekend(?:s)?|12[\s-]?hour|10[\s-]?hour|8[\s-]?hour)\b", re.I)
SCHEDULE_RE = re.compile(r"\b(\d\s*x\s*12s?|\d+\s+on\s*/\s*\d+\s+off|\d+\s*(?:x|×)\s*\d+\s*(?:hr|hour)s?|\d{1,2}\s*(?:hrs?|hours)\s*(?:/|per)\s*(?:wk|week))\b", re.I)


def guess_shift(*texts: str) -> str | None:
    for t in texts:
        if not t:
            continue
        m = SHIFT_RE.search(t)
        if m:
            w = m.group(1).lower().replace(" ", "-")
            mapping = {"day": "Days", "days": "Days", "night": "Nights", "nights": "Nights", "evening": "Evenings",
                       "evenings": "Evenings", "rotating": "Rotating", "variable": "Variable",
                       "weekend": "Weekends", "weekends": "Weekends"}
            return mapping.get(w, m.group(1))
    return None


def guess_schedule(*texts: str) -> str | None:
    for t in texts:
        if not t:
            continue
        m = SCHEDULE_RE.search(t)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
    return None


SALARY_RE = re.compile(
    r"(?:\$\s?\d[\d,]*(?:\.\d{2})?\s*(?:k|K)?(?:\s*(?:-|to|–|—)\s*\$?\s?\d[\d,]*(?:\.\d{2})?\s*(?:k|K)?)?"
    r"(?:\s*(?:per|/|an|a)\s*(?:hour|hr|year|yr|annum|annually))?)",
)


def find_salary(text: str) -> str | None:
    if not text:
        return None
    for m in SALARY_RE.finditer(text):
        s = m.group(0).strip()
        ctx = text[max(0, m.start() - 120): m.end() + 40].lower()
        if re.search(r"sign[\s-]*on|bonus|relocation|tuition|reimburse", ctx) and not re.search(r"salary|pay range|compensation|hourly|rate", ctx):
            continue
        if re.search(r"salary|pay|compensation|range|hourly|rate|annual|per hour|/hr|per year", ctx):
            return s
    return None


def summarize(title: str, hospital_name: str, setting_label: str, text: str, tags: list[str]) -> str:
    """Deterministic 2-3 sentence summary (no AI). Uses facts only; no copied sentences beyond short phrases."""
    first = f"{title} at {hospital_name}." if hospital_name else f"{title}."
    bits = []
    if setting_label and setting_label != "setting not stated":
        bits.append(f"Setting: {setting_label}.")
    notable = [t for t in tags if t in ("sign-on bonus", "new grad friendly", "fellowship", "relocation assistance",
                                         "loan repayment", "CPNP-AC required", "CPNP-AC preferred", "FNP accepted", "nights")]
    if notable:
        bits.append("Notable: " + ", ".join(notable) + ".")
    return " ".join([first] + bits[:2])
