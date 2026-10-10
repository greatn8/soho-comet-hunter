#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

SOURCE_DIR="${1:-results/v9_visual}"
REALTIME_DIR="${REALTIME_DIR:-$HOME/comethunting/comet_hunter_native_cuda_v7_archive/results/realtime}"
LOCK_FILE=".dashboard_publish.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Dashboard publisher is already running; skipping this cycle."
  exit 0
fi
cleanup_lock() {
  flock -u 9 2>/dev/null || true
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

LIVE_MEDIA_STAGE="${COMET_LIVE_MEDIA_STAGE:-/dev/shm/soho_comet_dashboard_live_media}"
export COMET_LIVE_MEDIA_STAGE="$LIVE_MEDIA_STAGE"

python3 tools/update_dashboard.py \
  --source "$SOURCE_DIR" \
  --results-root results \
  --realtime-source "$REALTIME_DIR"

# Publish the current stitched review clips on an ephemeral branch. The branch
# is rebuilt from scratch on every cycle in /dev/shm, so old video blobs do
# not accumulate in the main checkout or its Git history.
if [[ -d "$LIVE_MEDIA_STAGE" ]] && find "$LIVE_MEDIA_STAGE" -type f -print -quit | grep -q .; then
  LIVE_PUSH_DIR="$(mktemp -d /dev/shm/comet-live-media-push.XXXXXX)"
  cleanup_live_push() { rm -rf "$LIVE_PUSH_DIR"; }
  trap 'cleanup_live_push; cleanup_lock' EXIT INT TERM

  REMOTE_URL="$(git remote get-url origin)"
  git -C "$LIVE_PUSH_DIR" init -q
  git -C "$LIVE_PUSH_DIR" config user.name "Comet Dashboard Publisher"
  git -C "$LIVE_PUSH_DIR" config user.email "comet-dashboard@local"
  git -C "$LIVE_PUSH_DIR" remote add origin "$REMOTE_URL"
  cp -a "$LIVE_MEDIA_STAGE"/. "$LIVE_PUSH_DIR"/
  git -C "$LIVE_PUSH_DIR" add -A
  git -C "$LIVE_PUSH_DIR" commit -qm "Replace live comet review media"
  git -C "$LIVE_PUSH_DIR" branch -M live-media
  git -C "$LIVE_PUSH_DIR" push --force origin live-media
  rm -rf "$LIVE_PUSH_DIR"
  trap cleanup_lock EXIT INT TERM
fi

# Remove obsolete dashboard copies created by older publishers.
rm -rf docs/media/realtime_reviews docs/media/realtime_previews

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
