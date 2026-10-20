"""Push a digest of new jobs to ntfy.sh (and optionally email via SMTP).

python -m scraper.notify [--run data/runs/<date>.json] [--digest data/runs/<date>.md]

Env: NTFY_TOPIC (required for push; silent no-op if unset), NTFY_SERVER (default https://ntfy.sh),
     SITE_URL (view button), SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS/NOTIFY_EMAIL_TO (optional email).
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

import requests

from . import config


def digest_lines(new_jobs: list[dict], hospitals: dict[str, str], limit: int = 15) -> list[str]:
    lines = []
    ranked = sorted(new_jobs, key=lambda j: -(int(j.get("relevance") or 0)))
    for j in ranked[:limit]:
        h = hospitals.get(j.get("hospital_slug"), j.get("hospital_slug") or "")
        lines.append(f"[{j.get('relevance')}] {h}: {j.get('title')}\n{j.get('url')}")
    if len(ranked) > limit:
        lines.append(f"... and {len(ranked) - limit} more")
    return lines


def write_digest_md(path: Path, run_id: str, new_jobs: list[dict], hospitals: dict[str, str], summary: str) -> None:
    out = [f"# Peds NP jobs digest {run_id}", "", summary, ""]
    if not new_jobs:
        out.append("No new jobs today.")
    for j in sorted(new_jobs, key=lambda j: -(int(j.get("relevance") or 0))):
        h = hospitals.get(j.get("hospital_slug"), j.get("hospital_slug"))
        posted = f", posted {j['posted_at']}" if j.get("posted_at") else ""
        out.append(f"- **{j.get('title')}** ({h}, relevance {j.get('relevance')}, {j.get('setting')}{posted})  ")
        out.append(f"  {j.get('url')}")
    path.write_text("\n".join(out) + "\n")


def send_ntfy(title: str, body: str, click: str | None = None) -> bool:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("notify: NTFY_TOPIC not set; skipping push")
        return False
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    headers = {"Title": title.encode("utf-8").decode("latin-1", "ignore"), "Tags": "baby,hospital", "Priority": "default"}
    site = click or config.SITE_URL
    if site:
        headers["Click"] = site
        headers["Actions"] = f"view, Open job board, {site}"
    try:
        r = requests.post(f"{server}/{topic}", data=body.encode("utf-8"), headers=headers, timeout=20)
        print(f"notify: ntfy {r.status_code}")
        return r.ok
    except Exception as e:  # noqa: BLE001
        print(f"notify: ntfy failed: {e}")
        return False


def send_email(subject: str, body: str) -> bool:
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("NOTIFY_EMAIL_TO")
    if not (host and to):
        return False
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, to
    msg["From"] = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER") or "jobs@localhost"
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
            s.starttls()
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
            s.send_message(msg)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"notify: email failed: {e}")
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge-stats", default=None, help="JSON written by scripts/merge_jobs.py --stats-out")
    ap.add_argument("--ledger", default=str(config.LEDGER))
    ap.add_argument("--always", action="store_true", help="push even when there are 0 new jobs")
    a = ap.parse_args(argv)
    if not a.merge_stats or not Path(a.merge_stats).exists():
        print("notify: no merge stats; nothing to send")
        return 0
    st = json.loads(Path(a.merge_stats).read_text())
    new_jobs = st.get("new_jobs") or []
    try:
        hospitals = {h["slug"]: h.get("name", h["slug"]) for h in json.loads(Path(a.ledger).read_text()).get("hospitals", [])}
    except Exception:  # noqa: BLE001
        hospitals = {}
    if not new_jobs and not a.always:
        print("notify: 0 new jobs; no push")
        return 0
    top = [j for j in new_jobs if int(j.get("relevance") or 0) >= 2]
    title = f"Peds NP jobs: {len(new_jobs)} new" + (f" ({len(top)} peds)" if top else "")
    body = "\n\n".join(digest_lines(new_jobs, hospitals)) or "No new postings today."
    send_ntfy(title, body[:3900])
    send_email(title, body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
