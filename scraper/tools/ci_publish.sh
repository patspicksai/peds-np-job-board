#!/usr/bin/env bash
# Publish a directory of CI outputs to the orphan `ci-logs` branch under logs/<dest>.
# Usage: bash scraper/tools/ci_publish.sh <src_dir> <dest_subpath> [commit message]
# Runs inside a GitHub Actions checkout (uses the checkout's persisted credentials).
set -u
SRC="$1"; DEST="$2"; MSG="${3:-ci-logs: $DEST}"
WT="${RUNNER_TEMP:-/tmp}/cilogs-wt"
git config --global user.name "ci" >/dev/null
git config --global user.email "ci@users.noreply.github.com" >/dev/null
rm -rf "$WT"
if git fetch -q origin ci-logs 2>/dev/null; then
  git worktree add -q --detach "$WT" FETCH_HEAD
else
  git worktree add -q --detach "$WT" HEAD
  (cd "$WT" && git checkout -q --orphan ci-logs-new && git rm -rfq . >/dev/null 2>&1 || true)
fi
for attempt in 1 2 3 4 5; do
  mkdir -p "$WT/logs/$DEST"
  cp -r "$SRC"/. "$WT/logs/$DEST"/
  (
    cd "$WT" || exit 1
    git add -A logs
    git commit -qm "$MSG" || { echo "nothing to publish"; exit 0; }
    git push -q origin HEAD:refs/heads/ci-logs
  ) && { echo "published to ci-logs:logs/$DEST"; break; }
  echo "publish attempt $attempt failed; rebasing"
  (cd "$WT" && git fetch -q origin ci-logs && git reset -q --hard FETCH_HEAD)
done
git worktree remove --force "$WT" 2>/dev/null || true
exit 0
