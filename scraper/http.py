"""HTTP session: browser-like headers, retries, per-host throttle, timeouts, budget awareness."""
from __future__ import annotations

import os
import threading
import time
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config

try:  # Chrome TLS / HTTP2 fingerprint (passes some bot walls that block python-requests); optional
    from curl_cffi import requests as _creq
except Exception:  # noqa: BLE001
    _creq = None

BROWSER_HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}
JSON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


class FetchError(Exception):
    """Raised for a non-2xx response (keeps the status for the run report)."""

    def __init__(self, url: str, status: int | None, message: str = ""):
        self.url, self.status = url, status
        super().__init__(f"HTTP {status} for {url}{(': ' + message) if message else ''}")


class HostThrottle:
    """At most one request per `interval` seconds per host, across threads."""

    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._next: dict[str, float] = {}
        self._host_locks: dict[str, threading.Lock] = {}

    def wait(self, url: str) -> None:
        host = urlsplit(url).netloc.lower()
        with self._lock:
            hl = self._host_locks.setdefault(host, threading.Lock())
        with hl:
            now = time.monotonic()
            nxt = self._next.get(host, 0.0)
            if nxt > now:
                time.sleep(nxt - now)
            self._next[host] = time.monotonic() + self.interval


THROTTLE = HostThrottle(config.HOST_MIN_INTERVAL)
_local = threading.local()


def session() -> requests.Session:
    """One Session per thread (requests.Session is not thread safe)."""
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update(BROWSER_HEADERS)
        retry = Retry(total=config.RETRIES, connect=config.RETRIES, read=1, backoff_factor=1.5,
                      status_forcelist=(429, 502, 503, 504), allowed_methods=frozenset({"GET", "POST"}),
                      respect_retry_after_header=True, raise_on_status=False)
        ad = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
        s.mount("https://", ad)
        s.mount("http://", ad)
        _local.session = s
    return s


# Hosts where python-requests met a bot wall and curl_cffi (Chrome impersonation) got through: later requests to
# them go straight to curl_cffi. Hosts where both failed are remembered so the second attempt is not repeated.
IMPERSONATE_HOSTS: set[str] = set()
IMPERSONATE_FAILED: set[str] = set()
_imp_lock = threading.Lock()
BLOCK_STATUSES = (401, 403, 406, 429, 503)
# SCRAPER_IMPERSONATE: "auto" (default) retries bot-wall statuses once through curl_cffi; "off" never retries on
# its own (explicit impersonate=True calls still go through curl_cffi). The automatic retry is suspected of getting
# the runner's IP challenged by Cloudflare for the rest of the run (34 boards readable on 2026-10-20 came back
# 403 on 2026-10-21, the first run with the retry on), so the mode is an environment switch for A/B runs.
IMPERSONATE_MODE = os.environ.get("SCRAPER_IMPERSONATE", "auto").strip().lower() or "auto"


def cffi_available() -> bool:
    return _creq is not None


def auto_impersonate() -> bool:
    return _creq is not None and IMPERSONATE_MODE != "off"


def cffi_session():
    s = getattr(_local, "cffi", None)
    if s is None and _creq is not None:
        s = _creq.Session(impersonate="chrome")
        _local.cffi = s
    return s


def _cffi_request(method: str, url: str, headers: dict, kw: dict):
    """One request through curl_cffi with Chrome's own default headers (our UA / sec-ch-ua would contradict the
    impersonated fingerprint); caller headers (Accept, Referer, X-Requested-With, Content-Type) are kept."""
    s = cffi_session()
    hdrs = {k: v for k, v in headers.items() if k.lower() not in ("user-agent", "sec-ch-ua", "sec-ch-ua-mobile",
                                                                   "sec-ch-ua-platform", "accept-encoding")}
    kw2 = {k: v for k, v in kw.items() if k in ("params", "data", "json", "timeout", "allow_redirects", "cookies")}
    t = kw2.get("timeout")
    if isinstance(t, tuple):
        kw2["timeout"] = float(sum(t))
    last = None
    for attempt in range(1 + config.RETRIES):
        try:
            return s.request(method, url, headers=hdrs, **kw2)
        except Exception as e:  # noqa: BLE001  (curl_cffi raises its own RequestsError family)
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise FetchError(url, None, f"curl_cffi {type(last).__name__}: {str(last)[:200]}")


def request(method: str, url: str, *, json_api: bool = False, ok=(200,), impersonate: bool | None = None,
            **kw) -> requests.Response:
    """GET/POST with browser-like headers. impersonate: True = curl_cffi only, False = requests only,
    None (default) = requests, retried once with curl_cffi when the answer is a bot-wall status."""
    THROTTLE.wait(url)
    headers = dict(kw.pop("headers", {}) or {})
    if json_api:
        merged = dict(JSON_HEADERS)
        merged.update(headers)
        headers = merged
    kw.setdefault("timeout", (config.CONNECT_TIMEOUT, config.READ_TIMEOUT))
    host = urlsplit(url).netloc.lower()
    use_cffi = _creq is not None and (impersonate is True or (impersonate is None and host in IMPERSONATE_HOSTS))
    if use_cffi:
        r = _cffi_request(method, url, {**BROWSER_HEADERS, **headers}, kw)
    else:
        try:
            r = session().request(method, url, headers=headers, **kw)
        except requests.RequestException as e:
            # normalize network failures so adapters handle one exception type
            raise FetchError(url, None, f"{type(e).__name__}: {str(e)[:200]}") from e
        if (r.status_code in BLOCK_STATUSES and impersonate is None and auto_impersonate()
                and host not in IMPERSONATE_FAILED):
            try:
                r2 = _cffi_request(method, url, {**BROWSER_HEADERS, **headers}, kw)
            except FetchError:
                r2 = None
            with _imp_lock:
                if r2 is not None and r2.status_code not in BLOCK_STATUSES:
                    IMPERSONATE_HOSTS.add(host)
                    r = r2
                else:
                    IMPERSONATE_FAILED.add(host)
    if ok and r.status_code not in ok:
        raise FetchError(url, r.status_code, _short_reason(r))
    return r


def via(url: str) -> str:
    """'curl_cffi' when requests to this URL's host now go through Chrome impersonation, else 'requests'."""
    return "curl_cffi" if urlsplit(url).netloc.lower() in IMPERSONATE_HOSTS else "requests"


def get(url: str, **kw) -> requests.Response:
    return request("GET", url, **kw)


def post(url: str, **kw) -> requests.Response:
    return request("POST", url, **kw)


def get_json(url: str, **kw):
    r = request("GET", url, json_api=True, **kw)
    return r.json()


def post_json(url: str, payload, **kw):
    headers = {"Content-Type": "application/json"}
    headers.update(kw.pop("headers", {}) or {})
    r = request("POST", url, json_api=True, json=payload, headers=headers, **kw)
    return r.json()


def _short_reason(r) -> str:
    try:
        txt = (r.text or "")[:400]
    except Exception:  # noqa: BLE001
        txt = ""
    if "Just a moment" in txt or "cf-chl" in txt or "challenge-platform" in txt:
        return "bot challenge (Cloudflare)"
    if r.status_code == 403:
        return "forbidden (bot block?)"
    return txt.replace("\n", " ")[:160]


def is_bot_block(exc: Exception) -> bool:
    return isinstance(exc, FetchError) and exc.status in (401, 403, 406, 429, 503)
