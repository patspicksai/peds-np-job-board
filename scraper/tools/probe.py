"""Development probe: fetch careers pages from the Actions network and save raw evidence.

Usage: python -m scraper.tools.probe <urls.txt> <out_dir> [--no-browser] [--modes=req,cffi,chrome]

modes: req (requests, browser-like headers), cffi (curl_cffi Chrome TLS impersonation), pw (bundled Chromium
headless), chrome (installed Google Chrome, headed when $DISPLAY is set: run under xvfb-run). Default
"req,pw"; --no-browser drops the browser modes.

urls.txt lines: "<name> <url> [options...]" (blank lines and # comments ignored). Options:
  POST:<json>      send a JSON POST instead of a GET (req / cffi only)
  ACT:<json list>  browser actions after load, e.g. [{"click": "text=Staff RN"}, {"wait": 3000},
                   {"fill": ["input[type=search]", "nurse practitioner"]}, {"press": "Enter"}, {"goto": "url"}]
  MODES:<a,b>      only these modes for this line

For each URL and mode saves:
  <name>.<mode>.html  body (requests / curl_cffi) or rendered DOM (browser)
  <name>.<mode>.net.txt   every xhr/fetch/document request the browser saw, with status (and POST bodies)
  <name>.<mode>.xhrN.txt  JSON / text XHR bodies (first line: URL)
and writes summary.txt: status, final url, size, title, ATS signatures, iframes, ATS links, job-ish links.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import urljoin

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
    "ultipro": r"recruiting\d*\.ultipro\.com[^\"'\s<>]*|[a-z0-9.-]*ukg\.net[^\"'\s<>]*",
    "ukgready": r"[a-z0-9.-]*(?:entertimeonline|mykronos|saashr)\.com/ta/[^\"'\s<>]*",
    "talentbrew": r"tbcdn\.talentbrew\.com|radancy|TalentBrew",
    "brassring": r"brassring\.com[^\"'\s<>]*",
    "jibe": r"jibecdn\.com|jibeapply|/api/jobs\?",
    "avature": r"avature\.net[^\"'\s<>]*",
    "smartrecruiters": r"smartrecruiters\.com[^\"'\s<>]*",
    "jobvite": r"jobvite\.com[^\"'\s<>]*|talemetry[^\"'\s<>]*",
    "gr8people": r"gr8people\.com[^\"'\s<>]*",
    "cornerstone": r"csod\.com[^\"'\s<>]*",
    "paycom": r"paycomonline\.(?:net|com)[^\"'\s<>]*",
    "paylocity": r"recruiting\.paylocity\.com[^\"'\s<>]*",
    "adp": r"workforcenow\.adp\.com[^\"'\s<>]*|recruiting\.adp\.com[^\"'\s<>]*|myjobs\.adp\.com[^\"'\s<>]*",
    "healthcaresource": r"healthcaresource\.com[^\"'\s<>]*",
    "pageup": r"pageuppeople\.com[^\"'\s<>]*|/cw/en-us/",
    "infor": r"inforcloudsuite\.com[^\"'\s<>]*",
    "dayforce": r"dayforcehcm\.com[^\"'\s<>]*",
    "paycor": r"appone\.com[^\"'\s<>]*|paycor\.com[^\"'\s<>]*|newton\.newtonsoftware[^\"'\s<>]*",
    "silkroad": r"silkroad\.com[^\"'\s<>]*",
    "applicantpro": r"applicantpro\.com[^\"'\s<>]*",
    "jazzhr": r"applytojob\.com[^\"'\s<>]*|jazzhr[^\"'\s<>]*",
    "bamboohr": r"bamboohr\.com[^\"'\s<>]*",
    "greenhouse": r"greenhouse\.io[^\"'\s<>]*",
    "lever": r"lever\.co[^\"'\s<>]*",
    "catsone": r"catsone\.com[^\"'\s<>]*",
    "hrsmart": r"hrmdirect\.com[^\"'\s<>]*|hrsmart[^\"'\s<>]*|theapplicantmanager[^\"'\s<>]*",
    "cws": r"m-cloud\.io[^\"'\s<>]*|cws_opts",
    "jsonld_jobposting": r"\"@type\"\s*:\s*\"JobPosting\"",
}
ATS_HOST = re.compile(r"myworkdayjobs|myworkdaysite|icims\.com|oraclecloud\.com|taleo\.net|successfactors|ultipro\.com|"
                      r"ukg\.net|entertimeonline|mykronos|saashr|eightfold\.ai|brassring|avature|smartrecruiters|jobvite|"
                      r"talemetry|csod\.com|paycomonline|paylocity|workforcenow\.adp|recruiting\.adp|myjobs\.adp|"
                      r"healthcaresource|pageuppeople|inforcloudsuite|dayforcehcm|appone\.com|paycor|silkroad\.com|"
                      r"applicantpro|applytojob|bamboohr|greenhouse\.io|lever\.co|catsone|hrmdirect|ttcportals|"
                      r"jibe|phenompeople|m-cloud\.io|dejobs\.org|usnlx\.com|practicematch|recruiting\.com", re.I)
JOBISH_TEXT = re.compile(r"search (?:all )?(?:jobs|openings|positions|careers)|job (?:search|openings|opportunities|board|listings)|"
                         r"view (?:all )?(?:jobs|openings|positions|current)|current (?:openings|opportunities|jobs)|"
                         r"open positions|apply now|apply online|see (?:all )?(?:jobs|openings)|find (?:a )?(?:job|career)|"
                         r"explore (?:jobs|careers|opportunities)|careers?$|employment|join (?:our|the) team|nurse practitioner|"
                         r"advanced practice|provider (?:jobs|opportunities|careers)|physician", re.I)


def sigs(text: str) -> dict:
    out = {}
    for k, pat in SIGS.items():
        found = re.findall(pat, text or "", flags=re.I)
        if found:
            uniq = []
            for f in found:
                if f not in uniq:
                    uniq.append(f)
            out[k] = uniq[:5]
    return out


def link_facts(html: str, base: str) -> dict:
    """iframes, links to ATS hosts and job-search-looking links of a page."""
    try:
        from bs4 import BeautifulSoup
    except Exception:  # noqa: BLE001
        return {}
    soup = BeautifulSoup(html or "", "lxml")
    iframes = []
    for f in soup.find_all(["iframe", "frame"]):
        src = f.get("src") or f.get("data-src") or ""
        if src and not re.search(r"google|youtube|vimeo|facebook|doubleclick|recaptcha|hotjar|maps", src, re.I):
            iframes.append(urljoin(base, src))
    ats, jobish = [], []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "#")):
            continue
        u = urljoin(base, href)
        text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))[:70]
        if ATS_HOST.search(u):
            item = f"{text!r} {u}"
            if item not in ats:
                ats.append(item)
        elif JOBISH_TEXT.search(text) or re.search(r"/(?:jobs?|careers?|openings|positions)(?:[/?#]|$)", u, re.I):
            item = f"{text!r} {u}"
            if item not in jobish:
                jobish.append(item)
    t = soup.title.get_text(" ", strip=True)[:80] if soup.title else ""
    forms = [f"{f.get('method', 'get')} {urljoin(base, f.get('action') or '')} "
             f"[{','.join((i.get('name') or '') for i in f.find_all(['input', 'select'])[:8])}]"
             for f in soup.find_all("form")[:4]]
    return {"title": t, "iframes": iframes[:6], "ats_links": ats[:10], "jobish_links": jobish[:12],
            "anchors": len(soup.find_all("a")), "forms": forms}


def parse_entries(path: str) -> list[dict]:
    entries = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 2)
        e = {"name": parts[0], "url": parts[1], "post": None, "act": None, "modes": None}
        rest = parts[2] if len(parts) > 2 else ""
        # options: POST:{...}  ACT:[...]  MODES:a,b   (JSON values may contain spaces; each runs to the next option)
        idx =[(m.start(), m.group(1)) for m in re.finditer(r"(?:(?<=\s)|^)(POST|ACT|MODES):", rest)]
        for i, (pos, key) in enumerate(idx):
            end = idx[i + 1][0] if i + 1 < len(idx) else len(rest)
            val = rest[pos + len(key) + 1:end].strip()
            if key == "POST":
                e["post"] = json.loads(val)
            elif key == "ACT":
                e["act"] = json.loads(val)
            else:
                e["modes"] = [m.strip() for m in val.split(",") if m.strip()]
        entries.append(e)
    return entries


def write_facts(summary: list[str], text: str, base: str) -> None:
    summary.append(f"      sigs={json.dumps(sigs(text))[:1200]}")
    lf = link_facts(text, base)
    if lf:
        summary.append(f"      title={lf['title']!r} anchors={lf['anchors']} iframes={lf['iframes']}")
        if lf["ats_links"]:
            summary.append(f"      ats_links={lf['ats_links']}")
        if lf["jobish_links"]:
            summary.append(f"      jobish={lf['jobish_links']}")
        if lf["forms"]:
            summary.append(f"      forms={lf['forms']}")


def run_http(mode: str, entries: list[dict], out_dir: Path, summary: list[str]) -> None:
    if mode == "cffi":
        try:
            from curl_cffi import requests as creq
        except Exception as e:  # noqa: BLE001
            summary.append(f"[cffi] unavailable: {e}")
            return
        s = creq.Session(impersonate="chrome")
    else:
        s = requests.Session()
        s.headers.update(HEADERS)
    for e in entries:
        if e["modes"] and mode not in e["modes"]:
            continue
        name, url = e["name"], e["url"]
        t0 = time.time()
        try:
            if e["post"] is not None:
                r = s.post(url, json=e["post"], timeout=30,
                           headers={"Accept": "application/json", "Content-Type": "application/json"})
            else:
                r = s.get(url, timeout=30, allow_redirects=True)
            text = r.text
            (out_dir / f"{name}.{mode}.html").write_text(text[:3_000_000], errors="replace")
            hist = " -> ".join(str(h.status_code) for h in (getattr(r, "history", None) or []))
            summary.append(f"[{mode}] {name} {r.status_code} ({hist}) final={r.url} bytes={len(text)} "
                           f"ct={r.headers.get('content-type')} t={time.time()-t0:.1f}s")
            write_facts(summary, text, str(r.url))
        except Exception as ex:  # noqa: BLE001
            summary.append(f"[{mode}] {name} EXC {type(ex).__name__}: {str(ex)[:300]}")
        time.sleep(0.7)


SKIP_XHR = re.compile(r"analytics|track|telemetry|nr-data|google|pendo|doubleclick|facebook|hotjar|clarity|"
                      r"segment|optimizely|cookie|onetrust|newrelic|sentry|fonts|\.css|\.png|\.jpg|\.svg|\.woff", re.I)


def run_browser(mode: str, entries: list[dict], out_dir: Path, summary: list[str]) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        summary.append(f"[{mode}] unavailable: {e}")
        return
    with sync_playwright() as p:
        try:
            if mode == "chrome":
                browser = p.chromium.launch(channel="chrome", headless=not os.environ.get("DISPLAY"),
                                            args=["--disable-blink-features=AutomationControlled"])
                ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="en-US",
                                          timezone_id="America/New_York")
            else:
                browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
                ctx = browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 900}, locale="en-US")
            ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        except Exception:  # noqa: BLE001
            summary.append(f"[{mode}] FATAL launch " + traceback.format_exc()[-800:])
            return
        for e in entries:
            if e["post"] is not None or (e["modes"] and mode not in e["modes"]):
                continue
            name, url = e["name"], e["url"]
            net: list[str] = []
            bodies: list = []
            page = ctx.new_page()

            def on_resp(resp, net=net, bodies=bodies):
                try:
                    rt = resp.request.resource_type
                    if rt not in ("xhr", "fetch", "document"):
                        return
                    line = f"{resp.status} {resp.request.method} {rt} {resp.url}"
                    if resp.request.method == "POST":
                        line += f"\n    POSTDATA: {(resp.request.post_data or '')[:2000]}"
                    net.append(line)
                    if rt in ("xhr", "fetch") and not SKIP_XHR.search(resp.url):
                        ct = (resp.headers or {}).get("content-type", "")
                        if any(x in ct for x in ("json", "javascript", "xml", "text/html", "text/plain")):
                            bodies.append((resp.url, resp))
                except Exception:  # noqa: BLE001
                    pass

            page.on("response", on_resp)
            t0 = time.time()
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                for _ in range(6):
                    if "just a moment" in (page.title() or "").lower():
                        page.wait_for_timeout(3000)
                    else:
                        break
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:  # noqa: BLE001
                    pass
                page.wait_for_timeout(2000)
                for act in e["act"] or []:
                    try:
                        if "click" in act:
                            page.locator(act["click"]).first.click(timeout=8000)
                        elif "fill" in act:
                            page.locator(act["fill"][0]).first.fill(act["fill"][1], timeout=8000)
                        elif "press" in act:
                            page.keyboard.press(act["press"])
                        elif "goto" in act:
                            page.goto(act["goto"], wait_until="domcontentloaded", timeout=45000)
                        elif "wait" in act:
                            page.wait_for_timeout(int(act["wait"]))
                        elif "eval" in act:
                            net.append("EVAL: " + json.dumps(page.evaluate(act["eval"]))[:20000])
                        try:
                            page.wait_for_load_state("networkidle", timeout=8000)
                        except Exception:  # noqa: BLE001
                            pass
                        page.wait_for_timeout(1500)
                        net.append(f"ACTION {json.dumps(act)} -> {page.url}")
                    except Exception as ex:  # noqa: BLE001
                        net.append(f"ACTION FAILED {json.dumps(act)}: {type(ex).__name__}: {str(ex)[:200]}")
                html = page.content()
                frames = [f.url for f in page.frames if f.url and f.url != "about:blank"][:8]
                (out_dir / f"{name}.{mode}.html").write_text(html[:3_000_000], errors="replace")
                summary.append(f"[{mode}] {name} {resp.status if resp else None} final={page.url} "
                               f"bytes={len(html)} title={(page.title() or '')[:80]!r} t={time.time()-t0:.1f}s")
                summary.append(f"      frames={frames}")
                write_facts(summary, html, page.url)
                for fr in page.frames[1:4]:
                    try:
                        fh = fr.content()
                        if len(fh) > 500:
                            lf = link_facts(fh, fr.url)
                            summary.append(f"      frame {fr.url[:150]} ats={lf.get('ats_links', [])[:5]} "
                                           f"jobish={lf.get('jobish_links', [])[:6]}")
                    except Exception:  # noqa: BLE001
                        pass
            except Exception as ex:  # noqa: BLE001
                summary.append(f"[{mode}] {name} EXC {type(ex).__name__}: {str(ex)[:300]}")
            xl = []
            for bi, (burl, bresp) in enumerate(bodies[:20]):
                try:
                    btxt = bresp.text()
                    (out_dir / f"{name}.{mode}.xhr{bi}.txt").write_text(burl + "\n\n" + btxt[:600_000])
                    xl.append(f"{bresp.status} {burl[:160]} ({len(btxt)}b)")
                except Exception as ex:  # noqa: BLE001
                    net.append(f"body-capture-failed {burl} {ex}")
            if xl:
                summary.append("      xhr=" + json.dumps(xl[:14]))
            (out_dir / f"{name}.{mode}.net.txt").write_text("\n".join(net))
            page.close()
        browser.close()


def main() -> int:
    urls_file, out_dir = sys.argv[1], Path(sys.argv[2])
    modes = ["req", "pw"]
    for a in sys.argv[3:]:
        if a.startswith("--modes="):
            modes = [m.strip() for m in a.split("=", 1)[1].split(",") if m.strip()]
    if "--no-browser" in sys.argv:
        modes = [m for m in modes if m not in ("pw", "chrome")]
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = parse_entries(urls_file)
    summary: list[str] = []
    for mode in modes:
        t0 = time.time()
        if mode in ("req", "cffi"):
            run_http(mode, entries, out_dir, summary)
        else:
            run_browser(mode, entries, out_dir, summary)
        summary.append(f"== mode {mode} done in {time.time()-t0:.0f}s")
        (out_dir / "summary.txt").write_text("\n".join(summary) + "\n")
    print("\n".join(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
