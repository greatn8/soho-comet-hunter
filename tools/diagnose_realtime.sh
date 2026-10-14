#!/usr/bin/env bash
set -u

DASH_REPO="${DASH_REPO:-$HOME/comethunting/soho-comet-hunter-publish}"
PROJECT="${PROJECT:-$HOME/comethunting/comet_hunter_native_cuda_v7_archive}"
RT="$PROJECT/results/realtime"
HLOG="$PROJECT/logs/realtime_hunter.log"
PLOG="$DASH_REPO/logs/dashboard_publish.log"

section() { printf '\n===== %s =====\n' "$1"; }

section "CLOCK / HOST"
date -u
hostname -f 2>/dev/null || hostname

section "TMUX"
tmux ls 2>&1 || true

section "RELEVANT PROCESSES"
ps -eo pid,ppid,etime,stat,args | grep -E 'realtime_hunter.py|watch_dashboard_publish.sh|publish_dashboard.sh|comet_hunter_archive' | grep -v grep || true

section "REALTIME STATE FILES"
for f in heartbeat.json input_source.json last_detector_frame.txt detector_stats.json; do
  p="$RT/$f"
  if [[ -e "$p" ]]; then
    stat -c '%y %s bytes %n' "$p" 2>/dev/null || ls -l "$p"
    if [[ "$f" == "heartbeat.json" || "$f" == "input_source.json" || "$f" == "last_detector_frame.txt" ]]; then
      cat "$p"
      echo
    fi
  else
    echo "MISSING $p"
  fi
done

section "DASHBOARD GIT"
cd "$DASH_REPO" 2>/dev/null || { echo "MISSING dashboard repo: $DASH_REPO"; exit 0; }
git status --short --branch 2>&1 || true
git log -5 --date=iso --pretty='%h %ad %s' 2>&1 || true
echo "local HEAD:  $(git rev-parse HEAD 2>/dev/null || echo '?')"
echo "origin/main: $(git rev-parse origin/main 2>/dev/null || echo '?')"

section "PUBLISH LOCK"
LOCK="$DASH_REPO/.dashboard_publish.lock"
if [[ -e "$LOCK" ]]; then
  ls -l "$LOCK" 2>&1 || true
  if command -v lsof >/dev/null 2>&1; then
    lsof "$LOCK" 2>&1 || echo "No process currently holds lock file according to lsof."
  elif command -v fuser >/dev/null 2>&1; then
    fuser -v "$LOCK" 2>&1 || echo "No process currently holds lock file according to fuser."
  else
    echo "lsof/fuser unavailable."
  fi
else
  echo "No lock file present."
fi

section "REALTIME HUNTER LOG (LAST 100)"
tail -n 100 "$HLOG" 2>&1 || true

section "DASHBOARD PUBLISH LOG (LAST 140)"
tail -n 140 "$PLOG" 2>&1 || true

section "ERROR SUMMARY"
grep -Ei 'error|failed|fatal|conflict|timeout|timed out|permission denied|authentication|could not|no space|quota|killed' "$HLOG" "$PLOG" 2>/dev/null | tail -n 100 || true

section "END"
echo "Diagnostic complete."
