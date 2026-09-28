#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

INTERVAL="${1:-900}"
LOG="logs/dashboard_publish.log"
mkdir -p logs

echo "Dashboard publisher started. Interval: ${INTERVAL}s" | tee -a "$LOG"

while true; do
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] publish check" | tee -a "$LOG"
  bash ./publish_dashboard.sh results/v9_visual >> "$LOG" 2>&1 ||     echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] publish cycle failed; will retry" | tee -a "$LOG"
  sleep "$INTERVAL"
done
