#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Historical backfill is disabled. Starting realtime-only hunter instead."
exec "$SCRIPT_DIR/switch_to_realtime.sh" "${1:-$HOME/comethunting/comet_hunter_native_cuda_v7_archive}"
