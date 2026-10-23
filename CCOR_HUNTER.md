# Experimental NOAA CCOR1 and CCOR2 hunter

This is an **opt-in, independent** feed and image-motion screening workflow.
The existing SOHO/LASCO C3 CUDA pipeline, polling cooldown, publishing, historical archive and email alerts are unchanged.

The new script fetches NOAA SWPC timestamped Level-3 JPEG quicklooks from:
- https://services.swpc.noaa.gov/products/ccor1/jpegs/
- https://services.swpc.noaa.gov/products/ccor2/jpegs/

It attempts both the camera's jpegs.json index and its JPEG directory listing.
The code uses only discovered image filenames, preserving UTC image times.
Requires Python 3, Pillow and NumPy in the Bourbaki environment.

## Safe initial offline validation

    cd ~/comethunting/soho-comet-hunter-publish
    git pull --ff-only origin main
    python3 -m unittest tests/test_ccor_hunter.py -v
    python3 tools/ccor_hunter.py --project ~/comethunting/comet_hunter_native_cuda_v7_archive --offline --once

## Manual NOAA ingest and screening

    python3 tools/ccor_hunter.py --project ~/comethunting/comet_hunter_native_cuda_v7_archive --once

Once the first run succeeds, opt into a separate looping session:

    tmux new-session -d -s comet_ccor \
      "bash -lc 'cd ~/comethunting/soho-comet-hunter-publish && python3 tools/ccor_hunter.py --project ~/comethunting/comet_hunter_native_cuda_v7_archive --poll-seconds 920 >> ~/comethunting/comet_hunter_native_cuda_v7_archive/logs/ccor_hunter.log 2>&1'"

Stop:

    tmux kill-session -t comet_ccor

Review output:

    ~/comethunting/comet_hunter_native_cuda_v7_archive/results/ccor/ccor.json

The producer writes each camera's status and retained unverified motion tracks.
It does NOT send notifications, publish discovery reports, merge CCOR tracks
with existing C3 candidate IDs, or claim cross-camera duplicates. A timestamp
overlap between the two cameras is recorded as a review hint, never as proof
of a shared comet. Different instruments must be matched through calibrated
sky coordinates before any cross-camera duplicate suppression.

**Do not autostart or treat this as science-grade detection yet.**
Instrument pointing and plate scale, central occulting masks, background
systematics, star rejection and known-comet recall require calibration with
independently confirmed CCOR1 and CCOR2 tracks. The first release scans
bright positive temporal residual peaks in JPEG quicklooks and finds
consistent motions; it may miss faint comets and produce many artifacts.
Real-time ingestion availability, source content, precision and performance
must be checked on the Bourbaki host.

Do not use this script as evidence of a new or confirmed comet without
manual verification against original NOAA frames and the latest official
Sungrazer reports. The original C3 CUDA system must remain the priority.
