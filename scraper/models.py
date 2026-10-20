"""Job record (spec/jobs_schema.md), URL normalization and stable ids."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {"src", "source", "ref"}


def normalize_url(url: str) -> str:
    """Lowercase, drop tracking query params (utm_*, src, source, ref), drop fragment and trailing slash.

    Same rule as the Claude-driven path (spec/jobs_schema.md `id`), so both produce identical ids.
    """
    if not url:
        return ""
    url = url.strip()
    parts = urlsplit(url)
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not (k.lower().startswith("utm_") or k.lower() in TRACKING_PARAMS)]
    path = parts.path or ""
    while path.endswith("/"):
        path = path[:-1]
    out = urlunsplit((scheme, netloc, path, urlencode(query), ""))
    return out.lower()


def job_id(url: str) -> str:
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:12]


JOB_FIELDS = [
    "id", "hospital_slug", "title", "url", "apply_url", "source", "external_id", "location_text",
    "city", "state", "department", "setting", "employment_type", "shift", "schedule", "salary_text",
    "posted_at", "summary", "description", "requirements", "relevance", "relevance_reason", "tags",
    "first_seen_at", "last_seen_at", "status", "raw_notes",
]


@dataclass
class Listing:
    """A search-result row before details are fetched."""
    title: str
    url: str
    location_text: str = ""
    posted_at: str | None = None
    external_id: str | None = None
    snippet: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def norm(self) -> str:
        return normalize_url(self.url)


@dataclass
class Job:
    hospital_slug: str
    title: str
    url: str
    apply_url: str | None = None
    source: str = "employer"
    external_id: str | None = None
    location_text: str = ""
    city: str | None = None
    state: str | None = None
    department: str | None = None
    setting: str = "unspecified"
    employment_type: str | None = None
    shift: str | None = None
    schedule: str | None = None
    salary_text: str | None = None
    posted_at: str | None = None
    summary: str = ""
    description: str = ""
    requirements: list[str] = field(default_factory=list)
    relevance: int = 0
    relevance_reason: str = ""
    tags: list[str] = field(default_factory=list)
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    status: str = "active"
    raw_notes: str | None = None
    id: str | None = None

    def __post_init__(self) -> None:
        if not self.id and self.url:
            self.id = job_id(self.url)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {k: d.get(k) for k in JOB_FIELDS}
