#!/usr/bin/env bash
# Start independent NOAA CCOR screening on Bourbaki without interrupting C3.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="${1:-$HOME/comethunting/comet_hunter_native_cuda_v7_archive}"
if [[ "$(hostname -s)" != "bourbaki" ]]; then
  echo "ERROR: run this on Bourbaki; current host is $(hostname -s)." >&2
  exit 1
fi
if ! python3 -c 'import numpy, PIL' >/dev/null 2>&1; then
  echo "ERROR: Python packages numpy and pillow required in the active environment." >&2
  exit 1
fi
if ! command -v tmux >/dev/null 2>&1; then
  echo "ERROR: tmux not installed." >&2
  exit 1
fi
mkdir -p "$PROJECT/logs" "$PROJECT/results/ccor"
cd "$ROOT"
python3 -m unittest discover -s tests -p 'test_ccor_hunter.py' -v
# Download and validate a single batch before leaving a persistent worker.
python3 tools/ccor_hunter.py --project "$PROJECT" --once
python3 - "$PROJECT/results/ccor/ccor.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
if any(x.get("status") == "FEED_ERROR" for x in p["instruments"].values()):
    raise SystemExit("CCOR feed error; not starting persistent watcher")
print("CCOR first pass:",", ".join(k+": "+v["status"] for k,v in p["instruments"].items()))
PY
if tmux has-session -t comet_ccor 2>/dev/null; then
  echo "CCOR tmux session already exists; leaving it untouched."
  exit 0
fi
printf -v launch 'cd %q && exec python3 %q --project %q --poll-seconds 920 >> %q 2>&1' \
  "$ROOT" "$ROOT/tools/ccor_hunter.py" "$PROJECT" "$PROJECT/logs/ccor_hunter.log"
tmux new-session -d -s comet_ccor "bash -lc $(printf '%q' "$launch")"
echo "CCOR isolated watcher started. C3 watcher and CUDA process were not modified."
echo "Log: $PROJECT/logs/ccor_hunter.log"
echo "JSON: $PROJECT/results/ccor/ccor.json"
