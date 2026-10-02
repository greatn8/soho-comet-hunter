#!/usr/bin/env bash
set -euo pipefail

PROJECT="${1:-$HOME/comethunting/comet_hunter_native_cuda_v7_archive}"
DASH_REPO="${DASH_REPO:-$HOME/comethunting/soho-comet-hunter-publish}"
SESSION="comet_hybrid"
LOG="$PROJECT/logs/hybrid_hunter.log"
HUNTER="$DASH_REPO/tools/hybrid_hunter.py"

cd "$PROJECT"
mkdir -p logs state results/realtime

if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "ERROR: run this on bourbaki, not turing." >&2
  exit 1
fi
if ! nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | grep -q 'A100'; then
  echo "ERROR: A100 not detected. Run this on bourbaki." >&2
  exit 1
fi
if [[ ! -x ./comet_hunter_archive ]]; then
  echo "ERROR: missing $PROJECT/comet_hunter_archive" >&2
  exit 1
fi
if [[ ! -x ./run_archive_one_chunk.sh ]]; then
  echo "ERROR: missing $PROJECT/run_archive_one_chunk.sh" >&2
  exit 1
fi
if [[ ! -f "$HUNTER" ]]; then
  echo "ERROR: $HUNTER not found. Pull the dashboard repo first." >&2
  exit 1
fi

echo "Stopping old comet supervisors..."
for s in comet_realtime comet_handoff comet_archive comet_hybrid; do
  tmux kill-session -t "$s" 2>/dev/null || true
done
pkill -TERM -f 'realtime_hunter.py|hybrid_hunter.py|run_v10_incremental|run_archive_one_chunk|auto_verify_events|verify_event.sh' 2>/dev/null || true
sleep 3

date +%s > "$PROJECT/state/realtime_last_soho_session_epoch.txt"

tmux new-session -d -s "$SESSION"   "bash -lc 'cd "$PROJECT" && exec python3 "$HUNTER" --project "$PROJECT" >> "$LOG" 2>&1'"

sleep 1
echo
echo "Hybrid hunter started."
echo "Session: $SESSION"
echo "Log:     $LOG"
echo
echo "Realtime has priority. Historical backfill starts only when the newest live frame is at least 45 minutes old."
echo "Watch: tail -f $LOG"
