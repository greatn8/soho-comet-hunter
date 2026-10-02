#!/usr/bin/env bash
set -u
PROJECT="${1:-$HOME/comethunting/comet_hunter_native_cuda_v7_archive}"
echo "=== REALTIME TMUX ==="; tmux ls 2>/dev/null | grep -E 'comet_realtime|comet_handoff' || echo "No session found."
echo; echo "=== PROCESSES ==="; pgrep -af 'realtime_hunter.py|comet_hunter_archive|run_v10_incremental|run_archive_one_chunk|verify_event.sh' || true
echo; echo "=== LATEST REALTIME LOG ==="; tail -n 60 "$PROJECT/logs/realtime_hunter.log" 2>/dev/null || true
echo; echo "=== LATEST ALERT ==="; cat "$PROJECT/results/realtime/latest_alert.txt" 2>/dev/null || echo "No unmatched realtime alert yet."
echo; echo "=== ALERT HISTORY ==="; tail -n 10 "$PROJECT/results/realtime/alerts.tsv" 2>/dev/null || echo "No alert history yet."
echo; echo "=== KNOWN REPORT MATCHES ==="; tail -n 10 "$PROJECT/results/realtime/known_matches.tsv" 2>/dev/null || echo "No known-report matches yet."
