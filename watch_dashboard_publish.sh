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
  GIT_CONFIG_COUNT=1 \
  GIT_CONFIG_KEY_0=credential.helper \
  GIT_CONFIG_VALUE_0=store \
  timeout --signal=TERM --kill-after=30s 720s \
    bash ./publish_dashboard.sh results/v9_visual >> "$LOG" 2>&1
  rc=$?
  if [[ "$rc" -ne 0 ]]; then
    if [[ "$rc" -eq 124 || "$rc" -eq 137 ]]; then
      echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] publish cycle timed out; killed and will retry" | tee -a "$LOG"
    else
      echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] publish cycle failed rc=$rc; will retry" | tee -a "$LOG"
    fi
  fi
  sleep "$INTERVAL"
done
