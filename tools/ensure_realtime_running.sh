#!/usr/bin/env bash
# Recover a missing realtime hunter without interrupting a live CUDA process.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="${COMET_PROJECT:-$HOME/comethunting/comet_hunter_native_cuda_v7_archive}"
HUNTER="$ROOT/tools/realtime_hunter.py"
SESSION="comet_realtime"
LOG="$PROJECT/logs/realtime_hunter.log"
MODE_FILE="$PROJECT/results/realtime/mode.json"

if [[ "${COMET_AUTO_RECOVER:-1}" != "1" ]]; then
  echo "[INFO] Realtime hunter auto-recovery disabled."
  exit 0
fi

if [[ "${1:-}" != "--force" ]]; then
  # Deliberate switches to archive mode must not be undone by the publisher.
  if [[ ! -f "$MODE_FILE" ]]; then
    echo "[INFO] No realtime-only mode marker; not auto-starting a hunter."
    exit 0
  fi
  if ! python3 - "$MODE_FILE" <<'PY'
import json, sys
try:
    data=json.load(open(sys.argv[1],encoding="utf-8"))
    valid=data.get("mode")=="REALTIME_ONLY" and data.get("historical_backfill") is False
except (OSError,ValueError,TypeError):
    valid=False
sys.exit(0 if valid else 1)
PY
  then
    echo "[INFO] Hunter mode is not REALTIME_ONLY; not auto-starting."
    exit 0
  fi
fi

if [[ ! -f "$HUNTER" || ! -x "$PROJECT/comet_hunter_archive" ]]; then
  echo "[WARN] Realtime hunter Python code or CUDA binary is missing; cannot recover." >&2
  exit 1
fi
if ! command -v tmux >/dev/null 2>&1; then
  echo "[WARN] tmux is unavailable; cannot recover the hunter." >&2
  exit 1
fi

mkdir -p "$PROJECT/state" "$PROJECT/logs" "$PROJECT/results/realtime"
# The previous lock pathname can remain held forever by an orphan tmux
# launcher which inherited FD 8. Use a fresh lock namespace to recover from
# that existing leaked lock without deleting or mutating the old lock inode.
# All callers of THIS version use the new pathname, preserving mutual exclusion.
exec 8>"$PROJECT/state/.realtime_recover_v2.lock"
if ! flock -n 8; then
  echo "[WARN] Realtime recovery lock is busy; another recovery may be underway." >&2
  exit 1
fi

hunter_running() {
  # Check the *Python executable's argv*, not pgrep -f, because an old
  # 'tmux new-session ... python ...' wrapper can stay visible after Python
  # has died. This was observed on Bourbaki during the stale incident.
  python3 - "$HUNTER" "$PROJECT" <<'PY'
import os, pathlib, sys
hunter=os.path.realpath(sys.argv[1])
project=os.path.realpath(sys.argv[2])
for entry in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
    try:
        args=[os.fsdecode(x) for x in entry.read_bytes().split(b"\0") if x]
    except (OSError,PermissionError):
        continue
    if not args or not os.path.basename(args[0]).startswith("python"):
        continue
    # Project identity is stable across publishing checkout migrations.
    # Recognise the already running hunter from the old checkout too.
    if not any(os.path.basename(arg)=="realtime_hunter.py"
               for arg in args[1:5] if arg.endswith(".py")):
        continue
    if "--project" in args:
        index=args.index("--project")
        if index+1<len(args) and os.path.realpath(args[index+1])==project:
            sys.exit(0)
sys.exit(1)
PY
}

if hunter_running; then
  echo "[OK] Realtime hunter Python process is running; no restart needed."
  exit 0
fi

echo "[WARN] Realtime hunter missing; restarting it from the publisher watchdog."
tmux kill-session -t "$SESSION" 2>/dev/null || true
# An explicit persistent resolution setting is required for 1024 mode.
# Never silently fall back to 512 following a watchdog restart.
RESOLUTION_FILE="$PROJECT/state/realtime_c3_resolution.txt"
RESOLUTION="512"
if [[ -f "$RESOLUTION_FILE" ]]; then
  RESOLUTION="$(tr -d '[:space:]' < "$RESOLUTION_FILE")"
fi
if [[ "$RESOLUTION" != "512" && "$RESOLUTION" != "1024" ]]; then
  echo "[ERROR] Invalid persisted C3 resolution: $RESOLUTION" >&2
  exit 1
fi
# A normal launch resumes SOHO polling; never run --reprocess-cache-once
# automatically because a 14-day CUDA replay may take much longer.
printf -v launch 'cd %q && exec python3 %q --project %q --resolution %q >> %q 2>&1' \
  "$PROJECT" "$HUNTER" "$PROJECT" "$RESOLUTION" "$LOG"
# Critical: do not let tmux (or its server) inherit the recovery flock.
# Otherwise a detached tmux wrapper can hold FD 8 after this script exits,
# preventing every subsequent watchdog recovery.
tmux new-session -d -s "$SESSION" "bash -lc $(printf '%q' "$launch")" 8>&-

sleep 2
if hunter_running; then
  echo "[OK] Realtime hunter recovered in tmux session $SESSION."
else
  echo "[ERROR] Realtime hunter exited immediately after launch." >&2
  tail -30 "$LOG" >&2 || true
  exit 1
fi
