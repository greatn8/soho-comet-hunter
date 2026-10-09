#!/usr/bin/env bash
set -euo pipefail
PROJECT="${1:-$HOME/comethunting/comet_hunter_native_cuda_v7_archive}"
DASH_REPO="${DASH_REPO:-$HOME/comethunting/soho-comet-hunter-publish}"
SESSION="comet_realtime"
LOG="$PROJECT/logs/realtime_hunter.log"
HUNTER="$DASH_REPO/tools/realtime_hunter.py"
cd "$PROJECT"
mkdir -p logs state results/realtime
if [[ "$(hostname -s)" != "bourbaki" ]]; then
  echo "ERROR: run this on bourbaki, not turing." >&2
  exit 1
fi

gpu_ok=0
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | grep -q 'A100'; then
  gpu_ok=1
else
  # NVML/nvidia-smi can fail after a driver package update even while CUDA is
  # still usable. Fall back to the kernel's GPU model information.
  if grep -Rhs '^Model:.*A100' /proc/driver/nvidia/gpus/*/information 2>/dev/null | grep -q 'A100'; then
    gpu_ok=1
    echo "WARN: nvidia-smi/NVML is unavailable, but the A100 is present; continuing with CUDA."
  fi
fi
if [[ "$gpu_ok" -ne 1 ]]; then
  echo "ERROR: A100 not detected." >&2
  exit 1
fi
if [[ ! -x ./comet_hunter_archive ]]; then echo "ERROR: missing $PROJECT/comet_hunter_archive" >&2; exit 1; fi
if ! command -v ffmpeg >/dev/null 2>&1; then echo "ERROR: ffmpeg is required for live review videos." >&2; exit 1; fi
if [[ ! -f "$HUNTER" ]]; then echo "ERROR: $HUNTER not found. Pull dashboard repo first." >&2; exit 1; fi

echo "Stopping hybrid/historical workers..."
for s in comet_hybrid comet_handoff comet_archive comet_realtime; do tmux kill-session -t "$s" 2>/dev/null || true; done
pkill -TERM -f 'hybrid_hunter.py|realtime_hunter.py|run_v10_incremental|run_archive_one_chunk|auto_verify_events|verify_event.sh' 2>/dev/null || true
sleep 3

date +%s > "$PROJECT/state/realtime_last_soho_session_epoch.txt"
cat > "$PROJECT/results/realtime/mode.json" <<'EOF'
{
  "mode": "REALTIME_ONLY",
  "historical_backfill": false,
  "archive_policy": "New live detections age into the candidate archive; old archive crawling is disabled."
}
EOF

tmux new-session -d -s "$SESSION" "bash -lc 'cd "$PROJECT" && exec python3 "$HUNTER" --project "$PROJECT" >> "$LOG" 2>&1'"
sleep 1
echo "Realtime-only hunter started."
echo "Historical archive crawling is OFF."
echo "New live candidates are promoted into the candidate archive after 24 hours."
echo "Watch: tail -f $LOG"
