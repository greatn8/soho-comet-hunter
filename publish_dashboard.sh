#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

SOURCE_DIR="${1:-results/v9_visual}"
REALTIME_DIR="${REALTIME_DIR:-$HOME/comethunting/comet_hunter_native_cuda_v7_archive/results/realtime}"
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

python3 tools/update_dashboard.py \
  --source "$SOURCE_DIR" \
  --results-root results \
  --realtime-source "$REALTIME_DIR"

# Realtime review videos are transient local evidence. Older publisher
# versions copied them into docs/Git and caused unbounded repository growth.
rm -rf docs/media/realtime_reviews

git add -A docs

if git diff --cached --quiet; then
  echo "No dashboard changes to publish."
  exit 0
fi

STAMP="$(date -u +'%Y-%m-%d %H:%M:%SZ')"
git commit -m "Update comet dashboard $STAMP"

# Another publisher or a direct GitHub edit can land between the pull above
# and this push. Retry with a rebase instead of leaving the watcher stuck.
for attempt in 1 2 3; do
  if git push origin main; then
    echo "Published: https://greatn8.github.io/soho-comet-hunter/"
    exit 0
  fi

  echo "Push raced with another update; rebasing and retrying (attempt $attempt/3)..."
  git pull --rebase --autostash origin main
  sleep 2
done

echo "Dashboard push failed after 3 attempts." >&2
exit 1
