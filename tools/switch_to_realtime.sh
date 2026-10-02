#!/usr/bin/env bash
set -euo pipefail
PROJECT="${1:-$HOME/comethunting/comet_hunter_native_cuda_v7_archive}"
DASH_REPO="${DASH_REPO:-$HOME/comethunting/soho-comet-hunter-publish}"
SESSION="comet_realtime"
LOG="$PROJECT/logs/realtime_hunter.log"
cd "$PROJECT"; mkdir -p logs state results/realtime
if ! command -v nvidia-smi >/dev/null 2>&1; then echo "ERROR: run this on bourbaki, not turing." >&2; exit 1; fi
if ! nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | grep -q 'A100'; then echo "ERROR: A100 not detected." >&2; exit 1; fi
if [[ ! -x ./comet_hunter_archive ]]; then echo "ERROR: missing ./comet_hunter_archive" >&2; exit 1; fi
HUNTER="$DASH_REPO/tools/realtime_hunter.py"
if [[ ! -f "$HUNTER" ]]; then echo "ERROR: $HUNTER not found. Run git pull in $DASH_REPO first." >&2; exit 1; fi
if tmux has-session -t comet_handoff 2>/dev/null; then
  echo "Stopping historical comet_handoff session..."
  tmux send-keys -t comet_handoff C-c || true
  sleep 3
  tmux kill-session -t comet_handoff 2>/dev/null || true
fi
pkill -TERM -f 'run_v10_incremental|run_archive_one_chunk|auto_verify_events|verify_event.sh' 2>/dev/null || true
sleep 2

# Seed the realtime SOHO access gate at switch-over so the first realtime
# request cannot occur less than 15 minutes after an unknown final archive
# or verification request from the old pipeline.
GATE_STAMP="$PROJECT/state/realtime_last_soho_session_epoch.txt"
now_epoch="$(date +%s)"
if [[ ! -s "$GATE_STAMP" ]] || (( $(cat "$GATE_STAMP" 2>/dev/null || echo 0) < now_epoch )); then
  printf '%s\n' "$now_epoch" > "$GATE_STAMP"
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then echo "Realtime session already exists: $SESSION"; else
  tmux new-session -d -s "$SESSION" "bash -lc 'cd "$PROJECT" && exec python3 "$HUNTER" --project "$PROJECT" >> "$LOG" 2>&1'"
fi
sleep 1
echo "Realtime hunter started."
echo "Watch: tail -f $LOG"
