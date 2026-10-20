"""Development probe: which client gets through a bot wall?

Usage: python -m scraper.tools.cfprobe <urls.txt> <out_dir> [--modes requests,curl_cffi,pw_headless,pw_chrome,pw_chrome_headed,patchright]

urls.txt lines: "<name> <url>". For each mode and URL saves <mode>.<name>.html and writes summary.txt with
status, final URL, title, size, whether a JSON-LD JobPosting is present and elapsed seconds.
Headed modes need a display (run under xvfb-run on Actions). Modes whose package is missing are skipped.
"""
from __future__ import annotations

import re
import sys
import time
import traceback
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
}


def facts(html: str) -> str:
    t = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    title = re.sub(r"\s+", " ", t.group(1)).strip()[:70] if t else ""
    jp = bool(re.search(r'"@type"\s*:\s*"JobPosting"', html or ""))
    chal = "just a moment" in (html or "").lower()[:5000] or "challenge-platform" in (html or "")[:20000]
    return f"title={title!r} bytes={len(html or '')} jsonld={jp} challenge={chal}"


def run_requests(entries, out, summary):
    import requests
    s = requests.Session()
    s.headers.update(HEADERS)
    for name, url in entries:
        t0 = time.time()
        try:
            r = s.get(url, timeout=30)
            (out / f"requests.{name}.html").write_text(r.text[:2_000_000], errors="replace")
            summary.append(f"[requests] {name} {r.status_code} {facts(r.text)} t={time.time()-t0:.1f}s final={r.url}")
        except Exception as e:  # noqa: BLE001
            summary.append(f"[requests] {name} EXC {type(e).__name__}: {str(e)[:200]}")
        time.sleep(1)


def run_curl_cffi(entries, out, summary):
    try:
        from curl_cffi import requests as creq
    except Exception as e:  # noqa: BLE001
        summary.append(f"[curl_cffi] unavailable: {e}")
        return
    s = creq.Session(impersonate="chrome")
    for name, url in entries:
        t0 = time.time()
        try:
            r = s.get(url, timeout=30)
            (out / f"curl_cffi.{name}.html").write_text(r.text[:2_000_000], errors="replace")
            summary.append(f"[curl_cffi] {name} {r.status_code} {facts(r.text)} t={time.time()-t0:.1f}s final={r.url}")
        except Exception as e:  # noqa: BLE001
            summary.append(f"[curl_cffi] {name} EXC {type(e).__name__}: {str(e)[:200]}")
        time.sleep(1)


def run_browser(mode, entries, out, summary):
    try:
        if mode == "patchright":
            from patchright.sync_api import sync_playwright
        else:
            from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        summary.append(f"[{mode}] unavailable: {e}")
        return
    headed = mode in ("pw_chrome_headed", "patchright")
    channel = None if mode == "pw_headless" else "chrome"
    try:
        with sync_playwright() as p:
            kw = {"headless": not headed}
            if channel:
                kw["channel"] = channel
            if mode != "patchright":
                kw["args"] = ["--disable-blink-features=AutomationControlled"]
            if mode == "patchright":
                ctx = p.chromium.launch_persistent_context(user_data_dir=str(out / "pr-profile"), no_viewport=True, **kw)
            else:
                br = p.chromium.launch(**kw)
                ctx = br.new_context(locale="en-US", timezone_id="America/New_York",
                                     viewport={"width": 1366, "height": 900},
                                     **({} if channel else {"user_agent": UA}))
                ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            page = ctx.new_page()
            for name, url in entries:
                t0 = time.time()
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    status = resp.status if resp else None
                    passed_at = None
                    for i in range(10):
                        title = (page.title() or "").lower()
                        if "just a moment" in title or "attention required" in title:
                            page.wait_for_timeout(3000)
                        else:
                            passed_at = i
                            break
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:  # noqa: BLE001
                        pass
                    html = page.content()
                    (out / f"{mode}.{name}.html").write_text(html[:2_000_000], errors="replace")
                    summary.append(f"[{mode}] {name} {status} {facts(html)} cleared_after={passed_at} "
                                   f"t={time.time()-t0:.1f}s final={page.url}")
                except Exception as e:  # noqa: BLE001
                    summary.append(f"[{mode}] {name} EXC {type(e).__name__}: {str(e)[:200]}")
            ua = page.evaluate("navigator.userAgent")
            summary.append(f"[{mode}] navigator.userAgent={ua}")
            ctx.close()
    except Exception:  # noqa: BLE001
        summary.append(f"[{mode}] FATAL " + traceback.format_exc()[-1500:])


def main() -> int:
    urls_file, out = sys.argv[1], Path(sys.argv[2])
    modes = "requests,curl_cffi,pw_headless,pw_chrome,pw_chrome_headed,patchright"
    for a in sys.argv[3:]:
        if a.startswith("--modes="):
            modes = a.split("=", 1)[1]
    out.mkdir(parents=True, exist_ok=True)
    entries = []
    for line in Path(urls_file).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name, url = line.split(None, 1)
            entries.append((name, url.strip()))
    summary: list[str] = []
    for mode in [m.strip() for m in modes.split(",") if m.strip()]:
        t0 = time.time()
        if mode == "requests":
            run_requests(entries, out, summary)
        elif mode == "curl_cffi":
            run_curl_cffi(entries, out, summary)
        else:
            run_browser(mode, entries, out, summary)
        summary.append(f"== mode {mode} done in {time.time()-t0:.0f}s")
        (out / "summary.txt").write_text("\n".join(summary) + "\n")
    print("\n".join(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
