"""detect_platform(hospital): pick the adapter for a ledger hospital.

Order: explicit ats_config.adapter override -> host patterns in careers_search_url / careers_url / ats_config.host
-> careers_platform field -> "generic".
Host patterns win over the ledger's careers_platform because the ledger may record the underlying ATS
(e.g. "icims") while the public board is a front end with its own API (Jibe, TalentBrew) that the
icims adapter would only reach by redirect.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

HOST_RULES: list[tuple[str, str]] = [
    (r"\.myworkdayjobs\.com$|\.myworkdaysite\.com$", "workday"),
    (r"\.icims\.com$", "icims"),
    (r"\.fa\.[a-z0-9]+\.oraclecloud\.com$|\.fa\.ocs\.oraclecloud\.com$", "oracle_cloud"),
    (r"\.taleo\.net$", "taleo"),
    (r"^recruiting\d*\.ultipro\.com$|\.ukg\.net$", "ultipro"),
    (r"\.eightfold\.ai$", "eightfold"),
    (r"(?:^|\.)successfactors\.(?:com|eu)$|jobs\.sap\.com$", "successfactors"),
    (r"^jobs\.hopkinsmedicine\.org$", "hopkins"),
    (r"\.inforcloudsuite\.com$|\.cloud\.infor\.com$", "infor"),
    (r"^pm\.healthcaresource\.com$", "healthcaresource"),
]

PATH_RULES: list[tuple[str, str]] = [
    (r"/hcmUI/CandidateExperience/", "oracle_cloud"),
    (r"/careersection/", "taleo"),
    (r"/JobBoard/[0-9a-fA-F-]{36}", "ultipro"),
    (r"/careers-home(?:/|$)", "jibe"),
    (r"^/[a-z]{2}/[a-z]{2}(?:/|$)", "phenom"),
]

# ledger enum -> adapter
PLATFORM_MAP = {
    "workday": "workday", "icims": "icims", "taleo": "taleo", "successfactors": "successfactors",
    "phenom": "phenom", "ultipro": "ultipro", "oracle_cloud": "oracle_cloud", "oracle": "oracle_cloud",
    "eightfold": "eightfold", "jibe": "jibe", "talentbrew": "talentbrew", "radancy": "talentbrew",
    "symphony": "symphony", "careersite": "careersite", "talemetry": "careersite", "hopkins": "hopkins",
    "infor": "infor", "healthcaresource": "healthcaresource",
    # recognized but not specifically implemented: generic adapter
    "jobvite": "careersite", "greenhouse": "generic", "lever": "generic", "smartrecruiters": "generic",
    "pageup": "generic", "cornerstone": "generic", "adp": "generic", "avature": "generic", "dayforce": "generic",
    "paycom": "generic", "brassring": "generic", "custom": "generic", "unknown": "generic",
}


def _hosts_and_paths(h: dict) -> list[tuple[str, str]]:
    out = []
    cfg = h.get("ats_config") or {}
    if cfg.get("host"):
        out.append((str(cfg["host"]).lower().split(":")[0], ""))
    for k in ("careers_search_url", "careers_url"):
        u = h.get(k)
        if u:
            p = urlsplit(u)
            out.append((p.netloc.lower().split(":")[0], p.path))
    return out


def detect_platform(hospital: dict) -> str:
    cfg = hospital.get("ats_config") or {}
    if cfg.get("adapter"):
        return str(cfg["adapter"])
    # a hospital's own search page can wrap a HealthcareSource backend (Emerson, Rutland's HCTS portal)
    if "healthcaresource" in str(cfg.get("search_api") or cfg.get("backend_host") or cfg.get("backend_client_path") or "").lower():
        return "healthcaresource"
    for host, path in _hosts_and_paths(hospital):
        for pat, name in HOST_RULES:
            if re.search(pat, host):
                return name
    declared = (hospital.get("careers_platform") or "unknown").lower()
    for host, path in _hosts_and_paths(hospital):
        for pat, name in PATH_RULES:
            if path and re.search(pat, path):
                if name == "phenom" and declared not in ("phenom", "unknown", "custom"):
                    continue
                return name
    return PLATFORM_MAP.get(declared, "generic")


def board_key(hospital: dict, platform: str) -> str:
    """Hospitals that share one job board (system-wide ATS) are scraped once per run."""
    cfg = hospital.get("ats_config") or {}
    host = (cfg.get("host") or urlsplit(hospital.get("careers_search_url") or hospital.get("careers_url") or "").netloc).lower()
    extra = cfg.get("site") or cfg.get("site_number") or cfg.get("board_path") or cfg.get("career_section") or \
        cfg.get("client_path") or cfg.get("backend_client_path") or cfg.get("job_board") or ""
    if platform == "workday" and not extra:
        segs = [s for s in urlsplit(hospital.get("careers_search_url") or hospital.get("careers_url") or "").path.split("/") if s]
        segs = [s for s in segs if not re.match(r"^[a-z]{2}-[A-Z]{2}$", s)]
        extra = segs[0] if segs else ""
    if platform == "generic":
        # hospitals that point at the same careers page share one board (system sites: RWJBarnabas, MGB, HCA)
        url = hospital.get("careers_search_url") or hospital.get("careers_url") or ""
        p = urlsplit(url)
        if not p.netloc or re.search(r"google\.[a-z.]+/maps", url):
            return f"generic|{hospital.get('slug')}"
        return f"generic|{p.netloc.lower().removeprefix('www.')}{p.path.rstrip('/')}?{p.query}".lower()
    return f"{platform}|{host}|{extra}".lower()
