"""Shared browser (Playwright) for adapters that need a rendered DOM or have to pass a bot wall.

Playwright's sync API is bound to the thread that started it, so browser work runs on a small pool of
dedicated threads (config.BROWSER_WORKERS, each with its own browser) and adapter threads submit jobs
to it. This keeps memory bounded on the Actions runner.

Browser choice (SCRAPER_BROWSER=auto|chrome|chromium, default auto):
  auto     installed Google Chrome, headed, when `google-chrome` exists and a display is available
           (the workflows run the scraper under `xvfb-run`); otherwise Playwright's bundled Chromium headless.
  chrome   installed Google Chrome (headed if a display exists, else headless).
  chromium bundled Chromium, headless.
Verified on GitHub Actions (cfprobe run 39364436106): Cloudflare-fronted CareerSite tenants
(jobs.unchealthcare.org, careersatmainehealth.org) serve /search/jobs.json, the HTML search page and
/jobs/{id}.json to headed Chrome, while headless Chromium is flaky on the search pages.
"""
from __future__ import annotations

import os
import queue
import shutil
import threading
import time
from concurrent.futures import Future
from typing import Callable

from . import config
from .http import THROTTLE

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || {runtime: {}};
"""
_WEBDRIVER_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"


def _launch(pw):
    """-> (browser, context, kind). Real Chrome first when configured, bundled Chromium as the fallback."""
    mode = os.environ.get("SCRAPER_BROWSER", "auto").lower()
    has_display = bool(os.environ.get("DISPLAY"))
    errors = []
    if mode in ("auto", "chrome") and shutil.which("google-chrome") and (has_display or mode == "chrome"):
        try:
            br = pw.chromium.launch(channel="chrome", headless=not has_display,
                                    args=["--disable-blink-features=AutomationControlled"])
            ctx = br.new_context(locale="en-US", timezone_id="America/New_York", viewport={"width": 1366, "height": 900})
            ctx.add_init_script(_WEBDRIVER_JS)
            return br, ctx, f"chrome-{'headed' if has_display else 'headless'}"
        except Exception as e:  # noqa: BLE001
            errors.append(f"chrome: {type(e).__name__}: {str(e)[:160]}")
    br = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled",
                                                 "--no-sandbox", "--disable-dev-shm-usage"])
    ctx = br.new_context(user_agent=config.USER_AGENT, locale="en-US", timezone_id="America/New_York",
                         viewport={"width": 1366, "height": 900})
    ctx.add_init_script(_STEALTH_JS)
    return br, ctx, "chromium-headless" + (f" ({'; '.join(errors)})" if errors else "")


class BrowserUnavailable(Exception):
    pass


class _BrowserPool:
    """N worker threads, each owning its own Playwright + Chromium, consuming one shared job queue."""

    def __init__(self, size: int) -> None:
        self.size = max(1, size)
        self._q: queue.Queue = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self.failed: str | None = None
        self.renders = 0
        self.kind: str = ""

    def _ensure_threads(self) -> None:
        with self._lock:
            if not self._threads:
                for i in range(self.size):
                    t = threading.Thread(target=self._loop, name=f"browser-{i}", daemon=True)
                    t.start()
                    self._threads.append(t)

    def _loop(self) -> None:
        pw = br = ctx = None
        err = None
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            br, ctx, kind = _launch(pw)
            self.kind = self.kind or kind
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            self.failed = err
        while True:
            item = self._q.get()
            if item is None:
                break
            fn, fut = item
            if not fut.set_running_or_notify_cancel():
                continue
            if err:
                fut.set_exception(BrowserUnavailable(err))
                continue
            try:
                self.renders += 1
                fut.set_result(fn(ctx))
            except Exception as e:  # noqa: BLE001
                fut.set_exception(e)
        try:
            if br:
                br.close()
            if pw:
                pw.stop()
        except Exception:  # noqa: BLE001
            pass

    def submit(self, fn: Callable, timeout: float = 120):
        self._ensure_threads()
        fut: Future = Future()
        self._q.put((fn, fut))
        return fut.result(timeout=timeout)

    def shutdown(self) -> None:
        for _ in self._threads:
            self._q.put(None)
        for t in self._threads:
            t.join(timeout=20)
        self._threads = []


WORKER = _BrowserPool(config.BROWSER_WORKERS)


def render(url: str, *, wait_selector: str | None = None, click_more: bool = False, max_clicks: int = 10,
           capture_json: str | None = None, settle_ms: int = 2500, timeout_ms: int = 45000) -> dict:
    """Load `url` and return {"url", "status", "html", "json": [(url, text)...]}.

    capture_json: regex; JSON/XHR responses whose URL matches are captured (for APIs behind bot checks).
    click_more: click "load more"/"show more"/"next" buttons up to max_clicks times.
    """
    import re

    THROTTLE.wait(url)

    def job(ctx):
        page = ctx.new_page()
        captured: list = []
        if capture_json:
            pat = re.compile(capture_json, re.I)

            def on_resp(resp):
                try:
                    if pat.search(resp.url) and resp.request.resource_type in ("xhr", "fetch"):
                        captured.append(resp)
                except Exception:  # noqa: BLE001
                    pass
            page.on("response", on_resp)
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            status = resp.status if resp else None
            # Cloudflare / bot interstitials: a real browser either passes at once or after one auto-solve;
            # pages that stay on the challenge never clear (cfprobe), so give up after ~12 s.
            challenged = False
            for _ in range(4):
                title = (page.title() or "").lower()
                if "just a moment" in title or "attention required" in title or "checking your browser" in title:
                    challenged = True
                    page.wait_for_timeout(3000)
                else:
                    if challenged:
                        status = 200
                    challenged = False
                    break
            if challenged:
                return {"url": page.url, "status": 403, "html": page.content(), "title": page.title() or "",
                        "json": [], "blocked": True}
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:  # noqa: BLE001
                pass
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=12000)
                except Exception:  # noqa: BLE001
                    pass
            page.wait_for_timeout(settle_ms)
            if click_more:
                sel = ("button:has-text('Load more'), button:has-text('Show more'), a:has-text('Load more'), "
                       "a:has-text('Show more'), button:has-text('More jobs'), a:has-text('More Jobs'), "
                       "button:has-text('View more'), a[aria-label*='next' i], button[aria-label*='next' i]")
                for _ in range(max_clicks):
                    loc = page.locator(sel)
                    if loc.count() == 0:
                        break
                    try:
                        loc.first.click(timeout=4000)
                        page.wait_for_timeout(1800)
                        try:
                            page.wait_for_load_state("networkidle", timeout=8000)
                        except Exception:  # noqa: BLE001
                            pass
                    except Exception:  # noqa: BLE001
                        break
            html = page.content()
            title = page.title() or ""
            blocked = "just a moment" in title.lower() or "attention required" in title.lower()
            bodies = []
            for r in captured[:40]:
                try:
                    bodies.append((r.url, r.text()))
                except Exception:  # noqa: BLE001
                    pass
            return {"url": page.url, "status": 403 if blocked else status, "html": html, "title": title,
                    "json": bodies}
        finally:
            page.close()

    t0 = time.time()
    out = WORKER.submit(job, timeout=timeout_ms / 1000 + 120)
    out["seconds"] = round(time.time() - t0, 1)
    return out


def fetch_in_page(origin_url: str, api_urls: list[str], method: str = "GET", body: str | None = None,
                  timeout_ms: int = 45000) -> list[dict]:
    """Open origin_url (to pass bot checks / get cookies), then run fetch() for each api url in-page."""
    THROTTLE.wait(origin_url)

    def job(ctx):
        page = ctx.new_page()
        try:
            page.goto(origin_url, wait_until="domcontentloaded", timeout=timeout_ms)
            for _ in range(4):
                title = (page.title() or "").lower()
                if "just a moment" in title or "checking your browser" in title:
                    page.wait_for_timeout(3000)
                else:
                    break
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:  # noqa: BLE001
                pass
            results = []
            for u in api_urls:
                res = page.evaluate(
                    """async ([u, m, b]) => { try { const r = await fetch(u, {method: m, body: b,
                         headers: b ? {'Content-Type': 'application/json', 'Accept': 'application/json'} : {'Accept': 'application/json, text/html'},
                         credentials: 'include'});
                         return {status: r.status, text: await r.text()}; } catch (e) { return {status: 0, text: String(e)}; } }""",
                    [u, method, body])
                results.append({"url": u, **res})
                page.wait_for_timeout(int(config.HOST_MIN_INTERVAL * 1000))
            return results
        finally:
            page.close()

    return WORKER.submit(job, timeout=timeout_ms / 1000 + 60 + 5 * len(api_urls))


def shutdown() -> None:
    WORKER.shutdown()


def kind() -> str:
    """Which browser the pool launched ('' if no render happened yet)."""
    return WORKER.kind
