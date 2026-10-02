#!/usr/bin/env bash
set -u
PROJECT="${1:-$HOME/comethunting/comet_hunter_native_cuda_v7_archive}"
echo "=== HUNTER MODE ==="
cat "$PROJECT/results/realtime/mode.json" 2>/dev/null || echo '{"mode":"UNKNOWN"}'
echo
echo "=== TMUX ==="
tmux ls 2>/dev/null | grep -E 'comet_realtime|comet_hybrid|comet_handoff|comet_archive' || echo "No comet hunter session found."
echo
echo "=== PROCESSES ==="
pgrep -af 'realtime_hunter.py|hybrid_hunter.py|comet_hunter_archive|run_v10_incremental|run_archive_one_chunk|verify_event.sh' || true
echo
echo "=== LATEST REALTIME LOG ==="
tail -n 80 "$PROJECT/logs/realtime_hunter.log" 2>/dev/null || true
echo
echo "=== LIVE REVIEW PACKAGES ==="
find "$PROJECT/results/realtime/review" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | tail -n 20 || true
echo
echo "Old historical crawling: DISABLED"
echo "New live detections: promoted into candidate archive after 24 hours"
