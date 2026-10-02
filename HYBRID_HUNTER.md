# Realtime-only operating mode

Historical backfill is intentionally disabled.

The active workflow is now:

1. Watch newly arriving LASCO C3 data.
2. Run the CUDA detector whenever new frames arrive.
3. Compare comet-like tracks with recent Sungrazer reports.
4. Build local review media from the already-cached live frames.
5. Publish live candidates to the GitHub Pages dashboard.
6. After 24 hours from the last observation, promote live detections into the permanent candidate archive.

The old historical resume state is preserved but is not used automatically.

Start on bourbaki:

    cd ~/comethunting/soho-comet-hunter-publish
    git pull --rebase origin main
    ./tools/switch_to_realtime.sh ~/comethunting/comet_hunter_native_cuda_v7_archive

The old switch_to_hybrid.sh command remains only as a compatibility wrapper and now starts realtime-only mode.
