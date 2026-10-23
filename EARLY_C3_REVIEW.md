# C3 early review (opt-in)

This change **does not claim a three-frame comet detector**. Existing CUDA
requires at least five supporting observations. The standard 12/48/336 hour
passes, existing comet thresholds and notification workflow remain untouched.

## Purpose

The first successful short-window CUDA pass can now run a bounded, independent
pixel-verification and Sungrazer-report check **before** the slower CUDA
searches complete. It writes a separate JSON feed:

    PROJECT/results/realtime/early_candidates.json

Only already filtered tracks with strong pixel support, independent temporal
support, fresh image times and a **successful** recent C3 report check can
become `EARLY_ALERT_READY`. A report-source outage, an empty/unparseable
report list, a positive match, a reused physical trajectory, or a weak track
blocks early alert readiness. Results remain **unconfirmed**. No method can
guarantee that a candidate has never been reported elsewhere.

This is a speed experiment, **not proven earlier comet discovery**.
Budget added after the first CUDA pass is bounded to the first 60 ranked
tracks, but can still delay the other windows; measure cycle runtimes.

## Enable on Bourbaki

First pull current main. Only enable/restart when the C3 CUDA binary is idle;
**do not kill active CUDA work**.

    cd ~/comethunting/soho-comet-hunter-publish
    git pull --ff-only origin main
    python3 -m unittest discover -s tests -p test_early_c3_review.py -v

    PROJECT=~/comethunting/comet_hunter_native_cuda_v7_archive
    mkdir -p "$PROJECT/state"
    touch "$PROJECT/state/realtime_early_review_enabled"

The marker is read by `realtime_hunter.py` on every new short CUDA pass. The
running Python process must be restarted after `git pull` to load the code.
If the active C3 process is using the GPU, let it finish its current cycle.
With CUDA idle:

    tmux kill-session -t comet_realtime
    bash tools/ensure_realtime_running.sh --force

Check:

    tail -50 "$PROJECT/logs/realtime_hunter.log"
    python3 -m json.tool "$PROJECT/results/realtime/early_candidates.json" | head -120

The check only creates early review records; no new phone alerts are started.

## Optional early EMAIL notifications (GitHub)

The user's notification channel is EMAIL ONLY. The early C3 watcher dispatches
a GitHub Actions event; the existing strong-review workflow creates an
assigned GitHub issue, which sends the user's usual GitHub email.
There is NO ntfy notifier or phone-notification requirement.

**Enable only after** inspecting early_candidates.json and confirming the
short-window candidate filtering is working. The repository must be current on
Bourbaki and the GitHub CLI must already be authenticated as for the existing
`comet_github_alert.py` watcher.

    PROJECT=~/comethunting/comet_hunter_native_cuda_v7_archive
    cd ~/comethunting/soho-comet-hunter-publish
    python3 tools/watch_early_c3_email.py --project "$PROJECT" --once --dry-run
    tmux new-session -d -s comet_early_email \
      "bash -lc 'cd ~/comethunting/soho-comet-hunter-publish && exec python3 tools/watch_early_c3_email.py --project ~/comethunting/comet_hunter_native_cuda_v7_archive --interval 30 >> ~/comethunting/comet_hunter_native_cuda_v7_archive/logs/early_c3_email.log 2>&1'"

Unlike existing strong-review emails, early emails are eligible after the
12-hour CUDA pass before the longer passes. The GitHub receiver **fetches
official reports again** and fails closed on source errors, empty reports,
known matches, suspicious near-matches, or stale/weak candidates.

A local dispatch is not proof an email was actually delivered: check GitHub
Actions and notification settings. No direct email credentials or SMTP
configuration are added. All notices prominently say **UNCONFIRMED**.

## Disable

    rm -f ~/comethunting/comet_hunter_native_cuda_v7_archive/state/realtime_early_review_enabled
    tmux kill-session -t comet_early_email 2>/dev/null || true

The original C3 CUDA pipeline continues regardless.

## Limitations

The shortened pass still needs five tracked frames, not just three. Source
latency, GPU processing, and background contamination may prevent any early
candidate being eligible. Current quantitative thresholds are conservative
engineering guardrails, **not calibrated comet probabilities**. Replay against
independently labelled known SOHO comets is needed before treating this feature
as a measured improvement in sensitivity or speed.
