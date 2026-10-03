# Comet Hunter Project State

Last updated: 2026-10-03

This file is the continuity reference for the SOHO Comet Hunter project. It records the current operating mode, architecture, constraints, and next development priorities so work can continue without relying on chat history.

## Repositories and runtime

- GitHub repository: `greatn8/soho-comet-hunter`
- Dashboard/publisher checkout on Bourbaki: `/home/nshorter/comethunting/soho-comet-hunter-publish`
- CUDA project on Bourbaki: `/home/nshorter/comethunting/comet_hunter_native_cuda_v7_archive`
- Active CUDA source: `comet_hunter_archive.cu`
- Active detector binary: `comet_hunter_archive`
- Active realtime supervisor: `tools/realtime_hunter.py`
- Live review generator: `tools/live_review.py`
- Realtime tmux session: `comet_realtime`
- Realtime log: `logs/realtime_hunter.log`
- Realtime results: `results/realtime/`
- Rolling C3 cache: `/dev/shm/comet_realtime_nshorter_c3_512`

## Current operating mode

Realtime SOHO/LASCO C3 hunting has priority.

Historical archive crawling is intentionally disabled for now. The historical code and state are preserved so archive work can resume later, but it must not compete with realtime SOHO access or the live GPU detector.

The realtime hunter:

1. polls official SOHO C3 directory listings;
2. enforces a minimum ~15 minute interval between automated SOHO server sessions;
3. keeps a rolling recent C3 frame cache;
4. runs the CUDA detector when new frames arrive;
5. parses HIGH and MEDIUM moving tracks;
6. applies comet-like filtering;
7. checks recent Sungrazer reports for likely known matches;
8. creates live review media;
9. records unmatched candidates and alerts;
10. writes dashboard-facing realtime state.

Do not run an independent SOHO archive/verifier fetcher in parallel with the realtime hunter.

## Important realtime files

- `results/realtime/alerts.tsv`
- `results/realtime/known_matches.tsv`
- `results/realtime/latest_alert.txt`
- `results/realtime/latest_candidates.json`
- `results/realtime/detector_stats.json`
- `results/realtime/heartbeat.json`
- `results/realtime/seen_tracks.json`
- `results/realtime/review/`

## Current comet-like filter

`tools/realtime_hunter.py` currently rejects tracks using:

- fewer than 5 frames;
- RMS > 1.2;
- speed < 0.25 px/h;
- |vx| > 12 px/h or |vy| > 12 px/h;
- brightness CV > 1.2;
- sunward motion < 1.0 px/h;
- sunward/speed fraction < 0.35.

These filters should be preserved unless benchmark evidence justifies changing them.

## Current observed issue

The CUDA detector can produce hundreds of valid mathematical moving-track hypotheses in one live window. A recent run parsed 927 tracks and retained 89 after the existing comet-like filter.

Many surviving candidates occur in highly populated, quantised velocity families, for example vectors near:

- `vx ~ 7.5, vy ~ -10`
- `vx ~ 10, vy ~ -10`
- `vx ~ 5, vy ~ -7.5`
- `vx ~ 2.5, vy ~ -2.5`

This creates excessive review packages and unmatched realtime alerts.

The repeated velocity families also occur in historical/practice benchmark output, so velocity similarity alone must NOT be treated as proof that a candidate is false.

## Existing deduplication behavior

`same_track()` in `tools/realtime_hunter.py` extrapolates a prior track signature and considers it the same track when:

- projected position is within 8 pixels;
- velocity-vector difference is within 2 px/h;
- time separation is within 18 hours.

Accepted signatures are appended to `seen_tracks.json`, so this can suppress repeated instances across runs and later candidates in the same run.

`tools/live_review.py` has a similar review-package signature comparison using 10 pixels and 2.5 px/h.

Important: review generation currently occurs before the realtime loop exits on `dup`, so duplicate candidates can still log repeated `Live review media ready` messages even when the subsequent alert is suppressed.

## Next development priority

Improve realtime candidate discrimination without sacrificing discovery sensitivity.

Preferred direction:

- preserve the existing basic comet filter;
- distinguish exact/same-track duplicates from broader motion-family crowding;
- measure candidate-family or hypothesis density rather than rejecting solely by velocity;
- use time overlap, spatial relationship, trajectory overlap, shared/nearby detections, and ranking quality where possible;
- reduce alert spam while retaining candidates for review;
- validate any stronger suppression against known/practice Sungrazer cases before enabling it as a hard rejection rule.

Do not modify the CUDA detector merely to solve alert spam until the realtime post-processing layer has been tested.

## GitHub workflow

GitHub is the project source of truth for dashboard/publisher code.

Changes should be made as small coherent commits. After a GitHub-side code change, the Bourbaki checkout must be updated before the running process uses it. Runtime changes to `realtime_hunter.py` require a safe restart of the `comet_realtime` process after pulling the commit.

The assistant may make and commit agreed project changes directly to GitHub rather than asking for manual copy/paste edits.
