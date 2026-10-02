#!/usr/bin/env python3
"""Realtime-first SOHO C3 hunter with conservative historical backfill."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import signal
import subprocess
import time

import realtime_hunter as rt

STOP = False
ARCHIVE_PROC = None


def log(message):
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{stamp}] {message}", flush=True)


def utc_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path, payload):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_text(path):
    try:
        value = path.read_text(encoding="utf-8").strip()
        return value or None
    except Exception:
        return None


def archive_resume_date(project):
    return read_text(project / "state" / "next_date_c3_512.txt")


def newest_frame_age_minutes(cache_dir):
    frames = sorted(cache_dir.glob("*_c3_512.jpg"))
    if not frames:
        return None, None
    newest = frames[-1]
    try:
        observed = rt.image_time(newest.name)
    except Exception:
        return None, newest.name
    age = (dt.datetime.now(dt.timezone.utc) - observed).total_seconds() / 60.0
    return max(0.0, age), newest.name


def signal_handler(signum, frame):
    global STOP
    STOP = True
    rt.STOP = True
    log(f"Received signal {signum}; shutting down safely.")
    proc = ARCHIVE_PROC
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            pass


def write_state(results_dir, **changes):
    path = results_dir / "hybrid_state.json"
    state = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            state = loaded
    except Exception:
        pass
    state.update(changes)
    state["updated_at"] = utc_iso()
    atomic_json(path, state)
    return state


def reset_realtime_gate(project):
    gate = project / "state" / "realtime_last_soho_session_epoch.txt"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text(str(int(time.time())), encoding="utf-8")


def run_ranking(project):
    script = project / "run_v9_precision.sh"
    if not script.exists():
        log("Historical ranking script not found; skipping ranking.")
        return None
    rank_log = project / "logs" / "hybrid_rank.log"
    log("Running local historical precision ranking.")
    try:
        with rank_log.open("a", encoding="utf-8") as out:
            proc = subprocess.run(
                ["bash", str(script)],
                cwd=project,
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=900,
            )
        if proc.returncode != 0:
            log(f"Historical ranking returned status {proc.returncode}; continuing.")
        return proc.returncode
    except subprocess.TimeoutExpired:
        log("Historical ranking timed out after 15 minutes; continuing realtime.")
        return 124
    except Exception as exc:
        log(f"Historical ranking failed to start: {exc}")
        return None


def run_archive_chunk(project, results_dir, timeout_seconds):
    global ARCHIVE_PROC

    script = project / "run_archive_one_chunk.sh"
    if not script.exists():
        log(f"Archive worker missing: {script}")
        write_state(
            results_dir,
            mode="LIVE_PRIORITY",
            archive_status="missing_worker",
            archive_resume_date=archive_resume_date(project),
        )
        return 127

    before = archive_resume_date(project)
    archive_log = project / "logs" / "hybrid_archive_backfill.log"
    write_state(
        results_dir,
        mode="ARCHIVE_BACKFILL",
        archive_status="running",
        archive_started_at=utc_iso(),
        archive_resume_date=before,
    )
    log(f"Live C3 feed is stale; starting ONE historical chunk from {before or 'unknown'}.")

    rc = 1
    try:
        with archive_log.open("a", encoding="utf-8") as out:
            out.write("\n[" + utc_iso() + "] === HYBRID ARCHIVE BACKFILL START ===\n")
            out.flush()
            ARCHIVE_PROC = subprocess.Popen(
                ["bash", str(script)],
                cwd=project,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                text=True,
            )
            try:
                rc = ARCHIVE_PROC.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                log(f"Archive chunk exceeded {timeout_seconds}s; terminating to protect realtime.")
                try:
                    os.killpg(ARCHIVE_PROC.pid, signal.SIGTERM)
                    ARCHIVE_PROC.wait(timeout=20)
                except Exception:
                    try:
                        os.killpg(ARCHIVE_PROC.pid, signal.SIGKILL)
                    except Exception:
                        pass
                rc = 124
            out.write("[" + utc_iso() + f"] === HYBRID ARCHIVE BACKFILL END rc={rc} ===\n")
    except Exception as exc:
        log(f"Archive worker failed: {exc}")
        rc = 1
    finally:
        ARCHIVE_PROC = None

    reset_realtime_gate(project)
    after = archive_resume_date(project)
    rank_rc = run_ranking(project) if rc == 0 else None
    completed = rc == 0 and before != after

    state = write_state(
        results_dir,
        mode="LIVE_COOLDOWN",
        archive_status="completed" if rc == 0 else ("timeout" if rc == 124 else "failed"),
        archive_finished_at=utc_iso(),
        archive_return_code=rc,
        archive_rank_return_code=rank_rc,
        archive_resume_date=after,
        last_archive_success=completed,
    )
    count = int(state.get("archive_chunks_completed") or 0)
    if completed:
        write_state(results_dir, archive_chunks_completed=count + 1)
        log(f"Historical chunk complete. Resume state advanced: {before} -> {after}")
    else:
        log(f"Historical chunk did not advance state: rc={rc}, before={before}, after={after}")

    return rc


def main():
    parser = argparse.ArgumentParser(description="Realtime-first hunter with historical backfill")
    parser.add_argument("--project", required=True)
    parser.add_argument("--poll-seconds", type=int, default=905)
    parser.add_argument("--archive-idle-minutes", type=float, default=45.0)
    parser.add_argument("--archive-min-gap-minutes", type=float, default=90.0)
    parser.add_argument("--archive-timeout-seconds", type=int, default=2400)
    parser.add_argument("--window-hours", type=int, default=12)
    parser.add_argument("--compute-minutes", type=int, default=3)
    parser.add_argument("--min-frames", type=int, default=5)
    parser.add_argument("--max-candidates", type=int, default=300)
    parser.add_argument("--gpu-wait-seconds", type=int, default=720)
    args = parser.parse_args()

    if args.poll_seconds < 900:
        parser.error("--poll-seconds must be >=900")
    if args.archive_idle_minutes < 30:
        parser.error("--archive-idle-minutes must be >=30")

    project = Path(args.project).expanduser().resolve()
    results_dir = project / "results" / "realtime"
    state_dir = project / "state"
    cache_dir = Path("/dev/shm") / ("comet_realtime_" + os.environ.get("USER", "user") + "_c3_512")
    for directory in (results_dir, state_dir, cache_dir, project / "logs"):
        directory.mkdir(parents=True, exist_ok=True)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    detector_args = argparse.Namespace(
        window_hours=args.window_hours,
        compute_minutes=args.compute_minutes,
        min_frames=args.min_frames,
        max_candidates=args.max_candidates,
        gpu_wait_seconds=args.gpu_wait_seconds,
    )

    existing = write_state(
        results_dir,
        mode="STARTING",
        policy="realtime_first_archive_only_when_live_frame_stale",
        live_poll_seconds=args.poll_seconds,
        archive_idle_minutes=args.archive_idle_minutes,
        archive_min_gap_minutes=args.archive_min_gap_minutes,
        archive_timeout_seconds=args.archive_timeout_seconds,
        archive_resume_date=archive_resume_date(project),
    )

    last_archive_attempt_epoch = 0.0
    started = existing.get("archive_started_at")
    if started:
        try:
            last_archive_attempt_epoch = dt.datetime.fromisoformat(
                str(started).replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            pass

    log("Hybrid hunter starting. Realtime discovery has priority.")
    log(f"Historical backfill threshold: live frame stale >= {args.archive_idle_minutes:.0f} minutes.")

    while not STOP:
        cycle_start = time.time()

        try:
            write_state(
                results_dir,
                mode="LIVE_CHECK",
                archive_resume_date=archive_resume_date(project),
            )
            new_frames = rt.download_live_frames(
                cache_dir,
                state_dir,
                args.poll_seconds,
                int(args.window_hours),
            )
            if STOP:
                break

            rt.prune_cache(cache_dir, args.window_hours)
            if new_frames:
                rt.run_detector(project, cache_dir, results_dir, detector_args)

            age_minutes, newest_name = newest_frame_age_minutes(cache_dir)
            write_state(
                results_dir,
                mode="LIVE_PRIORITY",
                last_live_check_at=utc_iso(),
                last_live_download_count=len(new_frames),
                newest_live_frame=newest_name,
                newest_live_frame_age_minutes=round(age_minutes, 1) if age_minutes is not None else None,
                archive_resume_date=archive_resume_date(project),
            )
        except Exception as exc:
            log(f"Realtime cycle failed: {exc}")
            write_state(results_dir, mode="LIVE_ERROR", last_error=str(exc))

        if STOP:
            break

        elapsed = time.time() - cycle_start
        wait_seconds = max(5.0, args.poll_seconds - elapsed)
        log(f"Next action slot in about {int(wait_seconds)}s.")
        end = time.time() + wait_seconds
        while time.time() < end and not STOP:
            time.sleep(min(10.0, end - time.time()))
        if STOP:
            break

        age_minutes, newest_name = newest_frame_age_minutes(cache_dir)
        archive_gap_ok = (
            time.time() - last_archive_attempt_epoch
            >= args.archive_min_gap_minutes * 60.0
        )
        live_is_stale = age_minutes is None or age_minutes >= args.archive_idle_minutes

        if live_is_stale and archive_gap_ok:
            last_archive_attempt_epoch = time.time()
            run_archive_chunk(project, results_dir, args.archive_timeout_seconds)
        else:
            if age_minutes is not None and not live_is_stale:
                reason = f"live frame age {age_minutes:.1f}m < {args.archive_idle_minutes:.0f}m"
            else:
                reason = "archive minimum-gap timer"
            write_state(
                results_dir,
                mode="LIVE_PRIORITY",
                archive_status="armed_waiting_for_downtime",
                archive_wait_reason=reason,
                newest_live_frame=newest_name,
                newest_live_frame_age_minutes=round(age_minutes, 1) if age_minutes is not None else None,
                archive_resume_date=archive_resume_date(project),
            )

    write_state(results_dir, mode="STOPPED")
    log("Hybrid hunter stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
