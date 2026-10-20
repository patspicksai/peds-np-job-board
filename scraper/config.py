"""Static configuration: paths, keywords, relevance vocab, timeouts, budgets."""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
LEDGER = DATA / "hospitals.json"
JOBS = DATA / "jobs.json"
RUNS = DATA / "runs"

# Search keywords per board (brief: "Behaviour").
KEYWORDS = [
    "nurse practitioner",
    "pediatric nurse practitioner",
    "advanced practice provider",
    "APRN",
    "PICU",
    "NICU",
]

# Network behaviour
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/141.0.0.0 Safari/537.36")
HOST_MIN_INTERVAL = float(os.environ.get("SCRAPER_HOST_INTERVAL", "1.0"))  # seconds between hits per host
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 40
RETRIES = 2

# Budgets. RUN_BUDGET is the soft deadline (no new boards / pages / details after it); boards still running
# HARD_GRACE_SECONDS later are abandoned and the run file is written anyway. 34 min + 3 min grace + shutdown
# keeps the whole scrape step inside the workflow's hard 40 minute limit.
RUN_BUDGET_SECONDS = int(os.environ.get("SCRAPER_BUDGET", str(34 * 60)))
HARD_GRACE_SECONDS = int(os.environ.get("SCRAPER_HARD_GRACE", "180"))
HOSPITAL_BUDGET_SECONDS = int(os.environ.get("SCRAPER_HOSPITAL_BUDGET", str(9 * 60)))
WORKERS = int(os.environ.get("SCRAPER_WORKERS", "6"))
BROWSER_WORKERS = int(os.environ.get("SCRAPER_BROWSER_WORKERS", "2"))
MAX_PAGES_PER_KEYWORD = 10
MAX_DETAILS_PER_HOSPITAL = int(os.environ.get("SCRAPER_MAX_DETAILS", "60"))
DESCRIPTION_MAX = 6000

# Stale rule
STALE_DAYS = 7
SAFETY_MIN_RATIO = 0.5

SITE_URL = os.environ.get("SITE_URL", "https://patspicksai.github.io/peds-np-job-board/")
