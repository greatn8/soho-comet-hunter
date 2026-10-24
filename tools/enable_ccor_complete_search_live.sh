#!/usr/bin/env bash
# Enable complete seed search and global ranking in the live CCOR process.
# Only CCOR is restarted. C3, GPU jobs, data caches and alert workers unchanged.
# Python automatically falls back to legacy scan if the 5000-track budget hits.
set -euo pipefail

BASE="${HOME}/comethunting"
PROJECT="${BASE}/comet_hunter_native_cuda_v7_archive"
SCRIPT="${BASE}/soho-comet-hunter-publish/tools/ccor_hunter.py"
LOG="${PROJECT}/logs/ccor_complete_search.log"
SESSION="comet_ccor"

if [[ "$(hostname -s)" != "bourbaki" ]]; then
    echo "ERROR: Run on Bourbaki only, not $(hostname)" >&2
    exit 2
fi
if [[ ! -f "$SCRIPT" || ! -d "$PROJECT/results/ccor" ]]; then
    echo "ERROR: CCOR source or scientific results directory missing." >&2
    exit 2
fi
if ! grep -Fq 'ap.add_argument("--complete-search"' "$SCRIPT" ||
   ! grep -Fq 'fell_back_to_legacy' "$SCRIPT"; then
    echo "ERROR: Live source lacks complete-search/fallback code." >&2
    exit 2
fi
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "ERROR: Expected CCOR tmux session missing; preserving worker." >&2
    exit 2
fi

pattern="^python3 (-u )?${SCRIPT} --project ${PROJECT}([[:space:]]|$)"
mapfile -t workers < <(pgrep -f "$pattern" || true)
if (( ${#workers[@]} != 1 )); then
    echo "ERROR: Expected exactly 1 CCOR process, found ${#workers[@]}." >&2
    exit 2
fi
old_pid="${workers[0]}"
old_cmd=$(tr '\0' ' ' < "/proc/${old_pid}/cmdline")
if [[ "$old_cmd" == *" --complete-search "* ]]; then
    echo "Already enabled for CCOR PID $old_pid; no restart."
    exit 0
fi
if [[ "$old_cmd" == *" --offline "* || "$old_cmd" == *" --once "* ]]; then
    echo "ERROR: Unexpected non-live CCOR process; no action." >&2
    exit 2
fi
if [[ ! -d "$PROJECT/logs" ]]; then
    echo "ERROR: Existing project logs directory missing." >&2
    exit 2
fi

echo "Switching CCOR PID $old_pid to complete-search. C3 remains untouched."
kill -TERM "$old_pid"
for ((n=0; n<45; n++)); do
    if ! kill -0 "$old_pid" 2>/dev/null; then break; fi
    sleep 1
done
if kill -0 "$old_pid" 2>/dev/null; then
    echo "ERROR: Old CCOR still alive. No second worker launched." >&2
    exit 2
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION"
fi
printf -v launch 'cd %q && exec python3 -u %q --project %q --poll-seconds 920 --complete-search >> %q 2>&1' \
    "$PROJECT" "$SCRIPT" "$PROJECT" "$LOG"
tmux new-session -d -s "$SESSION" "$launch"

new_pid=""
new_cmd=""
for ((n=0; n<15; n++)); do
    mapfile -t workers < <(pgrep -f "$pattern" || true)
    if (( ${#workers[@]} == 1 )); then
        new_pid="${workers[0]}"
        new_cmd=$(tr '\0' ' ' < "/proc/${new_pid}/cmdline")
        if [[ "$new_cmd" == *" --complete-search "* ]]; then break; fi
    fi
    sleep 1
done
if [[ -z "$new_pid" || "$new_cmd" != *" --complete-search "* ]]; then
    echo "ERROR: New CCOR worker not found; inspect $LOG" >&2
    exit 2
fi
sleep 2
if ! kill -0 "$new_pid" 2>/dev/null; then
    echo "ERROR: New CCOR worker exited early; inspect $LOG" >&2
    exit 2
fi
echo "SUCCESS: Complete CCOR search is live, PID $new_pid."
echo "If search budget is reached, automatic legacy fallback remains active."
echo "CCOR log: $LOG"
echo "C3:"
pgrep -af '^python3 .*realtime_hunter.py' || true
echo "CCOR:"
pgrep -af "$pattern" || true
echo "TMUX:"
tmux list-panes -t "$SESSION" -F '#{pane_current_path} | #{pane_current_command}'
