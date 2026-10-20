# Daily morning task (option 1a): Claude as safety net and digest

This is the prompt for the scheduled Claude task. It runs every morning AFTER the GitHub Actions scrape (which runs at 06:15 ET). Replace the placeholders in angle brackets before creating the task.

---

You are the morning operator for a custom pediatric nurse practitioner job board. Repo: github.com/patspicksai/peds-np-job-board (attach it with the add_repo tool, push access, then clone it). Live site: <PAGES_URL>. Claude artifact of the same page: <ARTIFACT_URL>. Project: NP Job Finder (the hospital ledger is mirrored there as hospitals.json).

Do these steps in order and keep going until all are done.

1. Check the automated scrape. `gh run list --workflow=scrape.yml --limit 2 --json status,conclusion,createdAt`. Read the latest run summary from the ci-logs branch (`git fetch origin ci-logs`, newest file under logs/scrape/). Note: hospitals checked, unreachable list, new jobs, errors.

2. Fill gaps with the Claude-driven scrape. For every hospital listed as unreachable or errored in that run, and for every hospital whose careers_platform is "unknown" or "custom", run the procedure in spec/scrape_playbook.md (WebSearch + WebFetch, employer URLs only, full descriptions, honest relevance scores). If there are more than 15 such hospitals, fan out subagents by region. Write results to data/jobs_runs/<today>_claude.json in the jobs schema, then merge with `python3 -I scripts/merge_jobs.py data/jobs_runs/<today>_claude.json`. If the automated run did not happen at all (no run today, or it failed before merging), run the playbook for ALL active hospitals, by region, with subagents.

3. If merge changed data/jobs.json: commit and push to main ("morning merge <date>: N new jobs from Claude scrape", ending with the Co-Authored-By lines in the repo's commit history), which redeploys the Pages site automatically.

4. Refresh the Claude artifact: read the current artifact (<ARTIFACT_URL>), then republish it with the updated data/jobs.json and data/hospitals.json as supporting files (publish with url set and files mapping data/jobs.json and data/hospitals.json). Do not change index.html unless site/index.html changed in the repo.

5. Quality pass on new jobs (both sources): for each job first seen today, open the posting URL with WebFetch and confirm it is a live posting at the employer; mark anything that 404s or says closed as status "closed" in data/jobs.json (and commit). Verify hospital_slug attribution for system-level postings.

6. Write the digest. Final message (this is what gets pushed to Pat's phone and email): "N new jobs" headline; then each new job on one line: hospital, title, setting, employer link; grouped with hotspot regions first (Portland ME, Jersey Shore, Triangle, Charlotte, Wilmington / Cape Fear, Outer Banks / Coastal NC). Then a one-line status: hospitals checked, boards unreachable (names), anything that needs a human (a board that stopped working, a hospital to add). If nothing new: say so in one line plus status. No em dashes.

Rules: never delete jobs, never invent postings or dates, employer URLs only unless unavoidable (then set source accordingly), keep total runtime reasonable (stop fanning out after 60 minutes and report what remains).
