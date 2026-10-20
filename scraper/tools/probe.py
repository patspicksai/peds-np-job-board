"""Development probe: fetch careers pages from the Actions network and save raw evidence.

Usage: python -m scraper.tools.probe <urls.txt> <out_dir> [--no-browser]

urls.txt lines: "<name> <url>" (blank lines and # comments ignored). Optional third
token "POST:<json>" sends a JSON POST instead of a GET.

For each URL saves:
  <name>.req.html     raw body from requests (browser-like headers)
  <name>.pw.html      rendered DOM from Playwright (if browser enabled)
  <name>.net.txt      every xhr/fetch/document request Playwright saw, with status
and writes summary.txt (status, final url, size, ATS signatures).
"""
from __future__ import annotations

import json
import re
import sys
import time
import traceback
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

SIGS = {
    "workday": r"myworkdayjobs\.com[^\"'\s<>]*|myworkdaysite\.com[^\"'\s<>]*",
    "icims": r"[a-z0-9-]+\.icims\.com[^\"'\s<>]*",
    "oracle": r"[a-z0-9-]+\.fa\.[a-z0-9]+\.oraclecloud\.com[^\"'\s<>]*",
    "taleo": r"[a-z0-9-]+\.taleo\.net[^\"'\s<>]*",
    "successfactors": r"successfactors\.(?:com|eu)[^\"'\s<>]*|rmkcdn\.successfactors|jobs2web",
    "phenom": r"phenompeople|cdn\.phenompeople\.com|\"ddoKey\"|phApp",
    "eightfold": r"eightfold\.ai[^\"'\s<>]*",
    "ultipro": r"recruiting\.ultipro\.com[^\"'\s<>]*|ukg\.net",
    "talentbrew": r"tbcdn\.talentbrew\.com|radancy|TalentBrew",
    "brassring": r"brassring\.com[^\"'\s<>]*",
    "jibe": r"jibecdn\.com|jibeapply|/api/jobs\?",
    "avature": r"avature\.net[^\"'\s<>]*",
    "smartrecruiters": r"smartrecruiters\.com[^\"'\s<>]*",
    "jobvite": r"jobvite\.com[^\"'\s<>]*",
    "gr8people": r"gr8people\.com[^\"'\s<>]*",
    "cornerstone": r"csod\.com[^\"'\s<>]*",
    "paycom": r"paycomonline\.net[^\"'\s<>]*",
    "jsonld_jobposting": r"\"@type\"\s*:\s*\"JobPosting\"",
}


def sigs(text: str) -> dict:
    out = {}
    for k, pat in SIGS.items():
        found = re.findall(pat, text, flags=re.I)
        if found:
            uniq = []
            for f in found:
                if f not in uniq:
                    uniq.append(f)
            out[k] = uniq[:6]
    return out


def main() -> int:
    urls_file, out_dir = sys.argv[1], Path(sys.argv[2])
    use_browser = "--no-browser" not in sys.argv
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for line in Path(urls_file).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 2)
        entries.append(parts)
    summary = []
    s = requests.Session()
    s.headers.update(HEADERS)
    for parts in entries:
        name, url = parts[0], parts[1]
        extra = parts[2] if len(parts) > 2 else ""
        t0 = time.time()
        try:
            if extra.startswith("POST:"):
                body = json.loads(extra[5:])
                r = s.post(url, json=body, timeout=30,
                           headers={"Accept": "application/json", "Content-Type": "application/json"})
            else:
                r = s.get(url, timeout=30, allow_redirects=True)
            text = r.text
            (out_dir / f"{name}.req.html").write_text(text[:3_000_000], errors="replace")
            hist = " -> ".join(str(h.status_code) for h in r.history)
            summary.append(f"[req] {name} {r.status_code} ({hist}) final={r.url} bytes={len(text)} "
                           f"ct={r.headers.get('content-type')} t={time.time()-t0:.1f}s")
            summary.append(f"      sigs={json.dumps(sigs(text))[:1500]}")
        except Exception as e:  # noqa: BLE001
            summary.append(f"[req] {name} EXC {type(e).__name__}: {e}")
        time.sleep(1)

    if use_browser:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 900},
                                          locale="en-US")
                for parts in entries:
                    name, url = parts[0], parts[1]
                    if len(parts) > 2 and parts[2].startswith("POST:"):
                        continue
                    net = []
                    page = ctx.new_page()

                    def on_resp(resp, net=net):
                        try:
                            rt = resp.request.resource_type
                            if rt in ("xhr", "fetch", "document"):
                                line = f"{resp.status} {resp.request.method} {rt} {resp.url}"
                                if resp.request.method == "POST":
                                    pd = resp.request.post_data or ""
                                    line += f"\n    POSTDATA: {pd[:1500]}"
                                net.append(line)
                        except Exception:  # noqa: BLE001
                            pass

                    page.on("response", on_resp)
                    t0 = time.time()
                    try:
                        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                        try:
                            page.wait_for_load_state("networkidle", timeout=20000)
                        except Exception:  # noqa: BLE001
                            pass
                        page.wait_for_timeout(2500)
                        html = page.content()
                        (out_dir / f"{name}.pw.html").write_text(html[:3_000_000], errors="replace")
                        summary.append(f"[pw]  {name} {resp.status if resp else None} final={page.url} "
                                       f"bytes={len(html)} title={page.title()[:80]!r} t={time.time()-t0:.1f}s")
                        summary.append(f"      sigs={json.dumps(sigs(html))[:1500]}")
                    except Exception as e:  # noqa: BLE001
                        summary.append(f"[pw]  {name} EXC {type(e).__name__}: {str(e)[:300]}")
                    (out_dir / f"{name}.net.txt").write_text("\n".join(net))
                    page.close()
                browser.close()
        except Exception:  # noqa: BLE001
            summary.append("[pw] FATAL " + traceback.format_exc())

    (out_dir / "summary.txt").write_text("\n".join(summary) + "\n")
    print("\n".join(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
