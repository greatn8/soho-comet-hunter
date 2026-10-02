# Hybrid realtime and historical hunter

This supervisor keeps realtime LASCO C3 discovery as the priority and uses genuine live-feed downtime for historical backfill.

Default behaviour:

- Current C3 data is checked first at the existing 15 minute or greater cadence.
- New live frames are processed immediately by the CUDA detector.
- Historical network work is not allowed while the live stream is healthy.
- If the newest live C3 frame becomes at least 45 minutes old, one historical archive chunk may run at the next safe access slot.
- After an archive chunk, another full SOHO cooldown is forced before realtime access resumes.
- Historical v9 ranking runs locally after a successful archive chunk.
- Historical resume state remains in state/next_date_c3_512.txt.
- Historical verification downloads are not launched automatically while realtime discovery is the priority.

Start on bourbaki:

    cd ~/comethunting/soho-comet-hunter-publish
    git pull --rebase origin main
    ./tools/switch_to_hybrid.sh ~/comethunting/comet_hunter_native_cuda_v7_archive

Watch:

    tail -f ~/comethunting/comet_hunter_native_cuda_v7_archive/logs/hybrid_hunter.log

Status:

    ./tools/status_realtime_hunter.sh ~/comethunting/comet_hunter_native_cuda_v7_archive
