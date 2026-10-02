#!/usr/bin/env bash
set -u
PROJECT="${1:-$HOME/comethunting/comet_hunter_native_cuda_v7_archive}"

echo "=== HUNTER TMUX ==="
tmux ls 2>/dev/null | grep -E 'comet_hybrid|comet_realtime|comet_handoff|comet_archive' || echo "No comet hunter session found."

echo
echo "=== PROCESSES ==="
pgrep -af 'hybrid_hunter.py|realtime_hunter.py|comet_hunter_archive|run_v10_incremental|run_archive_one_chunk|verify_event.sh' || true

echo
echo "=== HYBRID STATE ==="
cat "$PROJECT/results/realtime/hybrid_state.json" 2>/dev/null || echo "No hybrid state yet."

echo
echo "=== LATEST HYBRID LOG ==="
tail -n 80 "$PROJECT/logs/hybrid_hunter.log" 2>/dev/null || true

echo
echo "=== LATEST REALTIME ALERT ==="
cat "$PROJECT/results/realtime/latest_alert.txt" 2>/dev/null || echo "No unmatched realtime alert yet."

echo
echo "=== HISTORICAL RESUME DATE ==="
cat "$PROJECT/state/next_date_c3_512.txt" 2>/dev/null || echo "No historical resume state."
