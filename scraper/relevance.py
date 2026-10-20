"""Relevance filter and scoring, exactly per spec/jobs_schema.md ("Relevance filter for the scrape").

Shared vocabulary with the Claude-driven path:
  keep = pediatric term (title or description)  AND  advanced practice role
  PA-only roles excluded unless the posting says "NP or PA".
  relevance 3 = explicitly pediatric AND acute / inpatient / PICU / NICU / peds ED / hospitalist / inpatient specialty
  relevance 2 = pediatric NP with unclear or outpatient setting, OR acute care NP at a children's hospital
  relevance 1 = NP role at a ledger hospital that is not clearly pediatric (kept only in hotspot regions)
  relevance 0 = not relevant
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- vocab
_I = re.I

APP_RE = re.compile(
    r"nurse[\s-]+practitioner|advanced[\s-]+practice[\s-]+(?:provider|professional|clinician|registered[\s-]+nurse|nurse|practitioner)"
    r"|\bAPRNs?\b|\bCRNPs?\b|\bAPNs?\b",
    _I,
)
# Upper-case acronyms only (avoid matching ordinary words); applied to the raw string.
APP_ACRONYM_RE = re.compile(r"\b(?:NPs?|APPs?|C?PNPs?|NNPs?|FNPs?|ACNPs?|AG-?ACNPs?|PNP-AC|PNP-PC|CPNP-AC|CPNP-PC|DNP)\b")
PA_RE = re.compile(r"(?i:physician[\s-]+assistant|physician[\s-]+associate)|\bPA-?C?s?\b")
NP_OR_PA_RE = re.compile(r"\bNP\s*(?:or|/|and|&)\s*PA\b|\bPA\s*(?:or|/|and|&)\s*NP\b|nurse practitioner\s*(?:or|/|and)\s*physician", _I)
NON_NP_APRN_RE = re.compile(r"\bCRNAs?\b|nurse[\s-]+anesthetist|midwi(?:fe|ves|fery)|\bCNMs?\b|clinical[\s-]+nurse[\s-]+specialist", _I)
NOT_A_JOB_RE = re.compile(r"\b(?:student|shadow(?:ing)?|preceptorship\s+for\s+students|volunteer)\b", _I)

PEDS_STRONG_RE = re.compile(
    r"p(?:a)?ediatric(?:s)?|\bpeds?\b|\bpedi\b|\bPICU\b|\bNICU\b|\bPCICU\b|\bCICU\b(?=.*(?:pediatric|child|congenital))"
    r"|neonat\w*|\bC?PNP\b|\bNNP\b|CPNP-?(?:AC|PC)|PNP-?(?:AC|PC)|congenital",
    _I,
)
PEDS_WEAK_RE = re.compile(r"children'?s|childrens|\bchild\b|adolescent|\binfants?\b|newborn|well[\s-]+baby|nursery", _I)

SETTING_PATTERNS = [
    ("picu", re.compile(r"\bPICU\b|\bPCICU\b|\bCICU\b|\bCVICU\b|(?:pediatric|peds?|cardiac)\s+(?:intensive|critical)\s+care|critical\s+care|intensive\s+care|\bICU\b|intermediate\s+care|\bIMCU\b|step[\s-]?down", _I)),
    ("nicu", re.compile(r"\bNICU\b|neonat\w*|\bNNP\b|newborn|nursery", _I)),
    ("peds_ed", re.compile(r"emergency|\bED\b|\bER\b|urgent\s+care|trauma", _I)),
    ("peds_inpatient", re.compile(r"hospitalist|hospital\s+medicine|inpatient|in-patient|acute\s+care|\bfloor\b|med(?:ical)?[\s/-]*surg", _I)),
]
SPECIALTY_RE = re.compile(
    r"cardiology|cardiac|cardiothoracic|heart|surgery|surgical|oncology|hematology|heme|onc\b|bone\s+marrow|transplant"
    r"|neurology|neurosurgery|nephrology|renal|gastro\w*|\bGI\b|hepatology|pulmonary|pulmonology|endocrin\w*"
    r"|orthop\w*|urology|otolaryngology|\bENT\b|plastic|rheumatology|infectious|immunology|allergy|genetics"
    r"|dermatology|ophthalmology|palliative|complex\s+care|rehab\w*|sleep|psychiatr\w*|behavioral|developmental"
    r"|adolescent\s+medicine|anesthesi\w*|sedation|pain|wound|burn|trauma\s+surgery|ECMO|dialysis|fetal",
    _I,
)
OUTPATIENT_RE = re.compile(r"outpatient|out-patient|ambulatory|clinic\b|clinics\b|office|practice\b", _I)
PRIMARY_RE = re.compile(r"primary\s+care|general\s+pediatrics|well[\s-]+child|pediatrics\s+(?:at|of)\b|pediatric\s+practice|school[\s-]+based", _I)

TAG_PATTERNS = [
    ("sign-on bonus", re.compile(r"sign[\s-]*on\s+bonus|signing\s+bonus|\$[\d,]+\s+sign[\s-]*on", _I)),
    ("new grad friendly", re.compile(r"new\s+grad(?:uate)?s?\b|fellowship|residency\s+program|transition\s+to\s+practice", _I)),
    ("fellowship", re.compile(r"\bfellowship\b", _I)),
    ("nights", re.compile(r"\bnights?\b|nocturn\w*|overnight", _I)),
    ("CPNP-AC required", re.compile(r"CPNP-?AC|PNP-?AC|acute\s+care\s+pediatric\s+nurse\s+practitioner|pediatric\s+acute\s+care\s+(?:nurse\s+practitioner|certification)", _I)),
    ("FNP accepted", re.compile(r"\bFNPs?\b|family\s+nurse\s+practitioner", _I)),
    ("relocation assistance", re.compile(r"relocation", _I)),
    ("union", re.compile(r"\bunion\b|collective\s+bargaining|\bSEIU\b|\bMNA\b", _I)),
    ("per diem", re.compile(r"per[\s-]+diem|\bPRN\b", _I)),
    ("part time", re.compile(r"part[\s-]+time", _I)),
    ("loan repayment", re.compile(r"loan\s+(?:repayment|forgiveness)", _I)),
]

REQ_RE = re.compile(
    r"CPNP|PNP|NNP|FNP|ACNP|AGACNP|\bAPRN\b|\bBLS\b|\bPALS\b|\bNRP\b|\bACLS\b|\bDEA\b|licens\w*|certif\w*"
    r"|years?\s+(?:of\s+)?(?:experience|clinical)|\bMSN\b|\bDNP\b|master'?s|board[\s-]+certif\w*|ANCC|PNCB|NCC\b",
    _I,
)

SETTING_LABELS = {
    "picu": "PICU / pediatric critical care",
    "nicu": "NICU / neonatology",
    "peds_inpatient": "pediatric inpatient / hospitalist",
    "peds_ed": "pediatric emergency",
    "peds_specialty_inpatient": "pediatric inpatient specialty service",
    "peds_specialty_outpatient": "pediatric outpatient specialty",
    "peds_primary_care": "pediatric primary care",
    "mixed": "mixed settings",
    "unspecified": "setting not stated",
}
ACUTE_SETTINGS = {"picu", "nicu", "peds_ed", "peds_inpatient", "peds_specialty_inpatient"}
CHILDRENS_TYPES = {"childrens_hospital", "specialty_pediatric"}


@dataclass
class Verdict:
    keep: bool
    relevance: int
    setting: str
    reason: str
    tags: list[str] = field(default_factory=list)
    pediatric: bool = False
    app_role: bool = False

    def as_tuple(self):
        return (self.keep, self.relevance, self.setting, self.reason, self.tags)


# ---------------------------------------------------------------- helpers

def is_app_role(title: str, text: str = "") -> tuple[bool, str]:
    """(is advanced-practice NP role, note). PA-only roles excluded unless the posting says NP or PA."""
    t = title or ""
    np_in_title = bool(APP_RE.search(t) or APP_ACRONYM_RE.search(t))
    if NOT_A_JOB_RE.search(t) and not re.search(r"fellowship|residency", t, _I):
        return False, "student / non-employment posting"
    if np_in_title:
        if NON_NP_APRN_RE.search(t) and not re.search(r"nurse[\s-]+practitioner|\bNP\b", t, _I):
            return False, "APRN role that is not a nurse practitioner (CRNA / CNM / CNS)"
        return True, ""
    if PA_RE.search(t):
        if NP_OR_PA_RE.search(text or "") or re.search(r"nurse[\s-]+practitioner", text or "", _I):
            return True, "PA title but posting accepts NPs"
        return False, "PA-only role"
    return False, "not an advanced practice role"


def title_is_app_candidate(title: str) -> bool:
    """Cheap listing-time check (title only). PA titles pass so details can confirm 'NP or PA'."""
    t = title or ""
    if APP_RE.search(t) or APP_ACRONYM_RE.search(t):
        return not (NON_NP_APRN_RE.search(t) and not re.search(r"nurse[\s-]+practitioner|\bNP\b", t, _I))
    return bool(PA_RE.search(t))


def peds_signal(title: str, text: str, location: str = "", department: str = "") -> tuple[int, str]:
    """0 none, 1 weak (description only), 2 explicit (title / department / location, or repeated in text)."""
    head = " ".join([title or "", department or "", location or ""])
    if PEDS_STRONG_RE.search(head) or PEDS_WEAK_RE.search(head):
        m = PEDS_STRONG_RE.search(head) or PEDS_WEAK_RE.search(head)
        return 2, f"'{m.group(0)}' in title/unit/location"
    body = text or ""
    strong = PEDS_STRONG_RE.findall(body)
    if len(strong) >= 2:
        return 2, f"pediatric terms in description ({', '.join(sorted(set(s.lower() for s in strong))[:3])})"
    weak = PEDS_WEAK_RE.findall(body)
    if strong or len(weak) >= 2:
        return 1, "pediatric mention in description"
    return 0, ""


def classify_setting(title: str, text: str, department: str = "") -> str:
    head = " ".join([title or "", department or ""])
    found = [name for name, pat in SETTING_PATTERNS if pat.search(head)]
    specialty_head = bool(SPECIALTY_RE.search(head))
    if not found and not specialty_head and PRIMARY_RE.search(head):
        return "peds_primary_care"
    if specialty_head and not found:
        body = text or ""
        inpatient = bool(re.search(r"inpatient|in-patient|consult\s+service|hospital\s+(?:service|unit|floor)|rounds?\b", body, _I))
        outpatient = bool(OUTPATIENT_RE.search(head) or re.search(r"outpatient|ambulatory|clinic", body, _I))
        if inpatient and outpatient:
            return "mixed"
        if inpatient:
            return "peds_specialty_inpatient"
        return "peds_specialty_outpatient"
    if len(found) == 1:
        if found[0] == "peds_inpatient" and specialty_head:
            return "peds_specialty_inpatient"
        return found[0]
    if len(found) > 1:
        # ICU + intermediate etc are still picu; neonatal ICU beats generic ICU
        if "nicu" in found:
            return "nicu" if all(f in ("nicu", "picu") for f in found) and re.search(r"neonat|NICU|NNP", head, _I) else "mixed"
        if set(found) <= {"picu", "peds_inpatient"}:
            return "picu"
        return "mixed"
    # nothing in the title: look at the description, but only for clear single-setting statements
    body = (text or "")[:3000]
    if PRIMARY_RE.search(body) and not re.search(r"inpatient|hospitalist|intensive", body, _I):
        return "peds_primary_care"
    hits = [name for name, pat in SETTING_PATTERNS[:3] if pat.search(body)]
    if len(hits) == 1:
        return hits[0]
    if re.search(r"hospitalist|inpatient\s+(?:unit|service|team)", body, _I):
        return "peds_inpatient"
    return "unspecified"


def find_tags(title: str, text: str) -> list[str]:
    blob = f"{title}\n{text or ''}"
    tags = [name for name, pat in TAG_PATTERNS if pat.search(blob)]
    if "CPNP-AC required" in tags and not re.search(r"requir|must|eligib", blob, _I):
        tags[tags.index("CPNP-AC required")] = "CPNP-AC preferred"
    return tags


def extract_requirements(text: str, limit: int = 12) -> list[str]:
    out: list[str] = []
    for raw in re.split(r"\n+|(?<=[.;])\s+(?=[A-Z])", text or ""):
        line = raw.strip(" \t-*•●▪–")
        if not (8 <= len(line) <= 260):
            continue
        if REQ_RE.search(line):
            if line not in out:
                out.append(line)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- main API

def classify(title: str, text: str = "", hospital: dict | None = None, location: str = "",
             department: str = "") -> Verdict:
    hospital = hospital or {}
    app, app_note = is_app_role(title, text)
    tags = find_tags(title, text)
    setting = classify_setting(title, text, department)
    if not app:
        return Verdict(False, 0, setting, app_note or "not an advanced practice role", tags, False, False)

    childrens = hospital.get("hospital_type") in CHILDRENS_TYPES
    hotspot = bool(hospital.get("hotspot"))
    sig, sig_note = peds_signal(title, text, location, department)
    pediatric = sig >= 1 or childrens
    explicit = sig >= 2 or childrens
    acute_words = bool(re.search(r"acute\s+care|inpatient|hospitalist|critical\s+care|intensive", f"{title} {department}", _I))

    if pediatric:
        if setting == "unspecified" and acute_words:
            setting = "peds_inpatient"
        acute = setting in ACUTE_SETTINGS or (setting == "mixed" and re.search(
            r"inpatient|ICU|intensive|critical|emergency|hospitalist|neonat|NICU|PICU", f"{title} {department} {text[:2000]}", _I))
        why = sig_note or "posting at a children's hospital"
        if explicit and acute:
            return Verdict(True, 3, setting, f"Pediatric acute care APP role ({SETTING_LABELS.get(setting, setting)}); {why}.", tags, True, True)
        if acute:
            return Verdict(True, 2, setting, f"Acute care APP role with pediatric mention only in the description; {why}.", tags, True, True)
        return Verdict(True, 2, setting, f"Pediatric APP role, {SETTING_LABELS.get(setting, setting)}; {why}.", tags, True, True)

    if hotspot:
        return Verdict(True, 1, "unspecified" if setting not in ("mixed",) else setting,
                       "NP role not clearly pediatric, kept because the hospital is in a hotspot region.", tags, False, True)
    return Verdict(False, 0, setting, "NP role not pediatric and hospital not in a hotspot region.", tags, False, True)


def is_candidate(title: str, text: str = "", hospital: dict | None = None, location: str = "",
                 department: str = ""):
    """Brief signature: -> (bool, relevance 0..3, setting, reason, tags)."""
    return classify(title, text, hospital, location, department).as_tuple()


def listing_priority(title: str, location: str = "", hospital: dict | None = None) -> int:
    """Listing-time triage before fetching details. 0 = skip, 1 = maybe, 2 = likely, 3 = strong."""
    if not title_is_app_candidate(title):
        return 0
    hospital = hospital or {}
    sig, _ = peds_signal(title, "", location)
    if sig >= 2 or hospital.get("hospital_type") in CHILDRENS_TYPES:
        return 3
    if hospital.get("hotspot"):
        return 2
    if re.search(r"acute|inpatient|hospitalist|critical|intensive|ICU|emergency|neonat", title, _I):
        return 1
    return 1
