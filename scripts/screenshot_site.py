#!/usr/bin/env python3
"""Screenshot a URL at desktop and phone sizes (full page), then click the first job card and shoot again.

Usage: python3 scripts/screenshot_site.py <url> <out_dir>
Writes desktop.png, mobile.png, desktop_card.png, mobile_card.png, desktop_coverage.png, mobile_coverage.png (plus _panel variants),
report.txt (console errors, timings).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

VIEWPORTS = {"desktop": {"width": 1440, "height": 900}, "mobile": {"width": 390, "height": 844}}
CARD_SELECTORS = [
    "[data-job-id]", ".job-card", ".job", "[class*='job-card']", "[class*='JobCard']", "article.job",
    "li.job", "[role=listitem]", "article", ".card",
]


def main() -> int:
    url, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    report = [f"url: {url}"]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, vp in VIEWPORTS.items():
            ctx = browser.new_context(viewport=vp, device_scale_factor=2 if name == "mobile" else 1,
                                      is_mobile=name == "mobile", has_touch=name == "mobile")
            page = ctx.new_page()
            errors: list[str] = []
            page.on("console", lambda m, errors=errors: errors.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e, errors=errors: errors.append(f"pageerror: {e}"))
            t0 = time.time()
            try:
                resp = page.goto(url, wait_until="networkidle", timeout=60000)
                report.append(f"[{name}] status={resp.status if resp else None} load={time.time() - t0:.1f}s title={page.title()!r}")
            except Exception as e:  # noqa: BLE001
                report.append(f"[{name}] goto error: {e}")
            page.wait_for_timeout(3000)
            page.screenshot(path=str(out / f"{name}.png"), full_page=True)
            clicked = None
            for sel in CARD_SELECTORS:
                loc = page.locator(sel)
                try:
                    if loc.count() > 0 and loc.first.is_visible():
                        loc.first.scroll_into_view_if_needed(timeout=3000)
                        loc.first.click(timeout=5000)
                        clicked = sel
                        break
                except Exception:  # noqa: BLE001
                    continue
            if clicked:
                page.wait_for_timeout(2000)
                page.screenshot(path=str(out / f"{name}_card.png"), full_page=True)
                report.append(f"[{name}] clicked first '{clicked}' and captured {name}_card.png")
            else:
                report.append(f"[{name}] no job card found to click")
            # the Coverage view (board health), opened through its hash so the capture works on any deploy that has it
            try:
                cov = page.locator('.view-seg [data-view="coverage"]')
                if cov.count() > 0:
                    page.evaluate("document.getElementById('scroller') && (document.getElementById('scroller').scrollTop = 0)")
                    cov.first.click(timeout=5000)
                    page.wait_for_timeout(1500)
                    page.screenshot(path=str(out / f"{name}_coverage.png"), full_page=False)
                    rows = page.locator("#coverage .crow").count()
                    report.append(f"[{name}] coverage view: {rows} rows, captured {name}_coverage.png")
                    first = page.locator("#coverage .crow").first
                    if rows and first.is_visible():
                        first.click(timeout=5000)
                        page.wait_for_timeout(1500)
                        page.screenshot(path=str(out / f"{name}_coverage_panel.png"), full_page=False)
                        report.append(f"[{name}] coverage row opened, captured {name}_coverage_panel.png")
                else:
                    report.append(f"[{name}] no Coverage tab on this page")
            except Exception as e:  # noqa: BLE001
                report.append(f"[{name}] coverage capture error: {e}")
            report.extend(f"[{name}] console {e}" for e in errors[:30])
            ctx.close()
        browser.close()
    (out / "report.txt").write_text("\n".join(report) + "\n")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
