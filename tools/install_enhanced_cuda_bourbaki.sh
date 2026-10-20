#!/usr/bin/env bash
# Run on Bourbaki. Build, back up, and enable enhanced detector.
# Does not kill the hunter or publisher. Requires idle CUDA before binary swap.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="${COMET_PROJECT:-$HOME/comethunting/comet_hunter_native_cuda_v7_archive}"
cd "$ROOT"
[[ "$(hostname -s)" == bourbaki ]] || { echo "ERROR: intended for Bourbaki" >&2; exit 1; }
[[ -x "$PROJECT/comet_hunter_archive" ]] || { echo "Missing production binary" >&2; exit 1; }
command -v nvcc >/dev/null || { echo "nvcc missing" >&2; exit 1; }
mkdir -p "$PROJECT/state" "$PROJECT/results/deploy_backups"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BIN="$PROJECT/comet_hunter_archive"
NEW="$PROJECT/results/deploy_backups/comet_hunter_enhanced_$STAMP"
BACKUP="$PROJECT/results/deploy_backups/comet_hunter_previous_$STAMP"
echo "Building isolated enhanced binary..."
nvcc -O3 -std=c++17 -arch=sm_80 --use_fast_math cuda/comet_hunter_archive.cu -o "$NEW" -lnvjpeg
chmod 755 "$NEW"
"$NEW" --help > "$NEW.help"
for FLAG in --within-window-stationary-filter --spatial-balanced-peaks --trajectory-integrity-filter --trajectory-null-control; do
  grep -q -- "$FLAG" "$NEW.help" || { echo "Compiled binary missing $FLAG" >&2; exit 1; }
done
g++ -std=c++17 -O2 -Icuda tests/test_spatial_peak_selector.cpp -o "$NEW.spatial_test"
"$NEW.spatial_test"
g++ -std=c++17 -O2 -Icuda tests/trajectory_integrity_filter_test.cpp -o "$NEW.integrity_test"
"$NEW.integrity_test"
echo "Checking CUDA hunter is not currently running..."
if pgrep -x comet_hunter_archive >/dev/null 2>&1; then
  echo "CUDA process running. Do NOT swap yet. Re-run this script between GPU passes." >&2
  exit 2
fi
cp -p "$BIN" "$BACKUP"
echo "Backup: $BACKUP"
# Atomic rename on same filesystem, and only after full checks.
STAGED="$PROJECT/.comet_hunter_enhanced_$STAMP"
cp "$NEW" "$STAGED"
chmod 755 "$STAGED"
# Recheck immediately before swap; no guarantee against another Python poll.
if pgrep -x comet_hunter_archive >/dev/null 2>&1; then
  rm -f "$STAGED"
  echo "CUDA became busy. Existing binary left untouched." >&2
  exit 2
fi
mv -f "$STAGED" "$BIN"
printf '%s\n' "$BACKUP" > "$PROJECT/state/realtime_cuda_previous_binary_path"
touch "$PROJECT/state/realtime_cuda_enhanced_enabled"
echo "ENHANCED BINARY INSTALLED. Flag enabled. Python hunter must be restarted to load current main runner if still running older code."
echo "Note: no processes restarted or interrupted by this script."
