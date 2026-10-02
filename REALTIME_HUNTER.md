# Realtime SOHO C3 hunter

The realtime hunter polls official LASCO C3 data at >=15-minute intervals, keeps a rolling 12-hour 512x512 cache in /dev/shm, runs the existing CUDA detector when a new frame arrives, and checks clean moving tracks against recent Sungrazer reports.

Start on bourbaki:

```bash
cd ~/comethunting/soho-comet-hunter-publish
git pull origin main
./tools/switch_to_realtime.sh ~/comethunting/comet_hunter_native_cuda_v7_archive
```

Watch:

```bash
tail -f ~/comethunting/comet_hunter_native_cuda_v7_archive/logs/realtime_hunter.log
```

Status:

```bash
~/comethunting/soho-comet-hunter-publish/tools/status_realtime_hunter.sh ~/comethunting/comet_hunter_native_cuda_v7_archive
```

The switch script stops the independent historical crawler first, preserving its resume state. This prevents two unrelated automated SOHO fetchers from violating the access cadence.

An UNMATCHED alert is not a discovery claim. Visually inspect and re-check official Sungrazer reports before submitting.
