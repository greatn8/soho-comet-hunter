#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

SOURCE_DIR="${1:-results/v9_visual}"
LOCK_FILE=".dashboard_publish.lock"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Dashboard publisher is already running; skipping this cycle."
  exit 0
fi

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "Source directory not found: $SOURCE_DIR" >&2
  exit 1
fi

echo "============================================================"
echo "Publishing comet dashboard"
echo "Source: $SOURCE_DIR"
echo "============================================================"

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
