"""HTTP session: browser-like headers, retries, per-host throttle, timeouts, budget awareness."""
from __future__ import annotations

import threading
import time
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config

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


def request(method: str, url: str, *, json_api: bool = False, ok=(200,), **kw) -> requests.Response:
    THROTTLE.wait(url)
    headers = dict(kw.pop("headers", {}) or {})
    if json_api:
        merged = dict(JSON_HEADERS)
        merged.update(headers)
        headers = merged
    kw.setdefault("timeout", (config.CONNECT_TIMEOUT, config.READ_TIMEOUT))
    try:
        r = session().request(method, url, headers=headers, **kw)
    except requests.RequestException as e:
        # normalize network failures so adapters handle one exception type
        raise FetchError(url, None, f"{type(e).__name__}: {str(e)[:200]}") from e
    if ok and r.status_code not in ok:
        raise FetchError(url, r.status_code, _short_reason(r))
    return r


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


def _short_reason(r: requests.Response) -> str:
    txt = (r.text or "")[:400]
    if "Just a moment" in txt or "cf-chl" in txt or "challenge-platform" in txt:
        return "bot challenge (Cloudflare)"
    if r.status_code == 403:
        return "forbidden (bot block?)"
    return txt.replace("\n", " ")[:160]


def is_bot_block(exc: Exception) -> bool:
    return isinstance(exc, FetchError) and exc.status in (401, 403, 406, 429, 503)
