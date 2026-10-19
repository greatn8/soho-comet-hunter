#!/usr/bin/env python3
"""Recover a small, ORIGINAL C3 image window for one known-comet control.

By default print a plan without contacting SOHO. --fetch downloads directly
from the official SOHO REPROCESSING/Completed archive, with the 15-minute
session cooldown shared with the realtime hunter. NEVER resize 512 to 1024.
Stop the realtime hunter and dashboard watchdog before using --fetch.

This is a controlled input-recovery utility, not a comet discovery verifier.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import html
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from benchmark_known_comets import controls, verify_dimensions, when

BASE = "https://soho.nascom.nasa.gov/data/REPROCESSING/Completed"
AGENT = "SOHO-Known-Comet-Benchmark/1.0 (research; respectful archive access)"
IMAGE = re.compile(r"\b(20\d{6}_\d{4}_c3_(?:512|1024)\.jpg)\b", re.I)
UTC = dt.timezone.utc


def day_folders(start, finish):
    day = start.date()
    while day <= finish.date():
        yield dt.datetime.combine(day, dt.time(), tzinfo=UTC).strftime("%Y/c3/%Y%m%d")
        day += dt.timedelta(days=1)


def select_links(html_content, start, finish, resolutions):
    allowed = set(resolutions)
    result = {}
    for filename in IMAGE.findall(html.unescape(html_content)):
        size = int(filename.rsplit("_", 1)[-1].split(".")[0])
        if size in allowed and start <= when(filename) <= finish:
            result[filename] = True
    return sorted(result)


def request(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


def hunter_alive():
    for p in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = [os.fsdecode(x) for x in p.read_bytes().split(b"\0") if x]
            if (args and Path(args[0]).name.startswith("python")
                    and any(Path(a).name == "realtime_hunter.py" for a in args[1:4])):
                return True
        except (OSError, ValueError):
            pass
    return False


def ensure_safe_to_fetch(project):
    if hunter_alive():
        raise RuntimeError("Realtime hunter is running. Stop its tmux session and "
                           "the dashboard publisher watchdog before fetching archive images.")
    for p in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = [os.fsdecode(x) for x in p.read_bytes().split(b"\0") if x]
            if args and Path(args[0]).name == "comet_hunter_archive":
                raise RuntimeError("CUDA detector busy; wait for its scan to finish.")
        except (OSError, ValueError):
            continue


def fetch_jpeg(url, dest, resolution, delay):
    if dest.is_file() and verify_dimensions(dest, resolution) == "ok":
        return "EXISTS"
    temp = dest.with_name(dest.name + ".part")
    temp.unlink(missing_ok=True)
    try:
        with request(url, timeout=90) as response, temp.open("wb") as output:
            size = 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > 8*1024*1024:
                    raise ValueError(f"Oversized JPEG (>8MiB): {url}")
                output.write(chunk)
        if size < 4096:
            raise ValueError(f"File too small for original SOHO JPEG: {dest.name}")
        verified = verify_dimensions(temp, resolution)
        if verified != "ok":
            raise ValueError(f"File dimension verification failed: {verified}")
        temp.replace(dest)
        return "DOWNLOADED"
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    finally:
        if delay > 0:
            time.sleep(delay)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--case", default="EV20260730T0530_1E9C7C",
                        help="Known event_id or unique substring of its known-object label")
    parser.add_argument("--resolution", choices=("512", "1024", "both"), default="both")
    parser.add_argument("--pad-hours", type=float, default=1.0)
    parser.add_argument("--max-images", type=int, default=120)
    parser.add_argument("--download-delay", type=float, default=0.25)
    parser.add_argument("--fetch", action="store_true",
                        help="Actually fetch SOHO listings/images (default: offline plan only)")
    parser.add_argument("--wait", action="store_true",
                        help="Wait for SOHO 900s cooldown, rather than exiting")
    parser.add_argument("--dashboard", type=Path, default=Path(__file__).resolve().parents[1] /
                        "docs/data/candidates.json")
    opts = parser.parse_args()
    project = opts.project.expanduser().resolve()
    if opts.pad_hours < 0 or opts.pad_hours > 3:
        parser.error("--pad-hours must be between zero and three")
    if opts.max_images < 5 or opts.max_images > 200:
        parser.error("--max-images must be 5..200")
    if opts.download_delay < 0.2:
        parser.error("--download-delay must be at least 0.2 seconds")
    matches = [x for x in controls(opts.dashboard)
               if opts.case in x["event_id"] or opts.case.lower() in x["known_object"].lower()]
    if len(matches) != 1:
        parser.error(f"--case must uniquely match one known-object event (got {len(matches)})")
    case = matches[0]
    resolutions = (512, 1024) if opts.resolution == "both" else (int(opts.resolution),)
    start = case["first"] - dt.timedelta(hours=opts.pad_hours)
    finish = case["last"] + dt.timedelta(hours=opts.pad_hours)
    folders = list(day_folders(start,finish))
    dest = project / "results" / "known_comet_inputs" / case["event_id"]
    plan = {"event_id":case["event_id"],"known_object":case["known_object"],
            "first":start.isoformat(),"last":finish.isoformat(),
            "resolutions":resolutions,"directories":[f"{BASE}/{x}/" for x in folders],
            "destination":str(dest)}
    print(json.dumps(plan,indent=2),flush=True)
    if not opts.fetch:
        print("PLAN ONLY: no SOHO network requests. Use --fetch to recover images.",flush=True)
        return 0
    if not project.is_dir():
        raise SystemExit(f"Project directory not found: {project}")
    if not shutil_which("ffprobe"):
        raise SystemExit("ffprobe required to verify original 512/1024 image dimensions.")
    ensure_safe_to_fetch(project)
    state = project / "state"
    state.mkdir(parents=True,exist_ok=True)
    gate = state/"realtime_soho_gate.lock"
    stamp = state/"realtime_last_soho_session_epoch.txt"
    # Hold the same flock until the complete small download finishes. Unlike
    # the realtime hunter, this worker deliberately reserves the full session.
    with gate.open("a+") as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        ensure_safe_to_fetch(project)
        try:
            last = int(stamp.read_text().strip())
        except (OSError,ValueError):
            last = 0
        wait = 900-(int(time.time())-last)
        if wait > 0 and not opts.wait:
            print(f"SOHO cooldown: {wait}s. Re-run with --wait.",flush=True)
            return 2
        while wait > 0:
            print(f"SOHO cooldown: {wait}s remaining.",flush=True)
            time.sleep(min(30,wait))
            wait = 900-(int(time.time())-last)
        stamp.write_text(str(int(time.time())))
        dest.mkdir(parents=True,exist_ok=True)
        valid = []
        errors = []
        for rel in folders:
            url=f"{BASE}/{rel}/"
            try:
                with request(url) as resp:
                    page=resp.read(1_500_000).decode("utf-8","replace")
                selected=select_links(page,start,finish,resolutions)
                for filename in selected:
                    if filename not in valid:
                        valid.append(filename)
            except (urllib.error.URLError,ValueError,OSError) as exc:
                errors.append(f"LISTING {url}: {exc}")
            time.sleep(opts.download_delay)
        valid=sorted(set(valid))
        if len(valid)>opts.max_images:
            print(f"Refusing {len(valid)} images, which exceeds --max-images={opts.max_images}. "
                  "Narrow the event interval instead of silently truncating.",flush=True)
            return 3
        done=skipped=0
        for filename in valid:
            day = filename[:8]
            url=f"{BASE}/{day[:4]}/c3/{day}/{filename}"
            resolution = int(filename.rsplit("_",1)[-1].split(".")[0])
            try:
                status=fetch_jpeg(url,dest/filename,resolution,opts.download_delay)
                done += status == "DOWNLOADED"
                skipped += status == "EXISTS"
            except (urllib.error.URLError,ValueError,OSError) as exc:
                errors.append(f"IMAGE {filename}: {exc}")
        result={"event_id":case["event_id"],"expected_listed":len(valid),
                "downloaded":done,"already_present":skipped,"errors":errors}
        (dest/"recovery_report.json").write_text(json.dumps(result,indent=2)+"\n",
                                                  encoding="utf-8")
        print(json.dumps(result,indent=2),flush=True)
        if len(valid)<5 or len(valid)-len(errors)<5:
            print("Insufficient recovered image inputs. Inspect listing errors.",flush=True)
            return 4
        return 0


def shutil_which(binary):
    import shutil
    return shutil.which(binary)


if __name__=="__main__":
    raise SystemExit(main())
