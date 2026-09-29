#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

SOURCE_DIR="${1:-results/v9_visual}"
LOCK_DIR=".dashboard_publish.lockdir"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Dashboard publisher is already running; skipping this cycle."
  exit 0
fi
cleanup_lock() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup_lock EXIT INT TERM

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "Source directory not found: $SOURCE_DIR" >&2
  exit 1
fi

echo "============================================================"
echo "Publishing comet dashboard"
echo "Source: $SOURCE_DIR"
echo "============================================================"

# Keep the shared checkout synchronized with direct GitHub changes.
# Rebase any local dashboard commits instead of letting the watcher become
# permanently stuck behind origin/main.
git pull --rebase --autostash origin main

python3 tools/update_dashboard.py   --source "$SOURCE_DIR"   --results-root results

git add docs

if git diff --cached --quiet; then
  echo "No dashboard changes to publish."
  exit 0
fi

STAMP="$(date -u +'%Y-%m-%d %H:%M:%SZ')"
git commit -m "Update comet dashboard $STAMP"
git push origin main

echo "Published: https://greatn8.github.io/soho-comet-hunter/"
