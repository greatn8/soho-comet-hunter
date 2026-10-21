#!/usr/bin/env python3
"""Probe one CURRENT SOHO near-realtime C3 GIF without modifying production output.

Uses the same 15-minute SOHO access gate as the realtime hunter. Does not
start CUDA, change detector state or download a full historical backlog.
"""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path

from realtime_hunter import (
    SOHO_NRT_GIF_BASE, discover_nrt_frames, fetch_bytes, soho_gate_wait,
)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", type=Path, required=True)
    args=ap.parse_args()
    project=args.project.expanduser().resolve()
    state=project/"state"
    state.mkdir(parents=True,exist_ok=True)
    print("Waiting for shared SOHO access cooldown if necessary...",flush=True)
    soho_gate_wait(state,905)
    now=dt.datetime.now(dt.timezone.utc)
    images=discover_nrt_frames(now,lookback_days=7,resolution=1024)
    if not images:
        print("FAIL: No recent C3 GIFs listed. Do NOT change production.",flush=True)
        return 2
    name,meta=max(images.items(),key=lambda kv:kv[1]["time"])
    age=(now-meta["time"]).total_seconds()/3600
    print(f"Latest feed frame: {name}; age={age:.1f}h; source={meta['url']}",flush=True)
    data=fetch_bytes(meta["url"])
    if len(data)<10000:
        print(f"FAIL: GIF too small ({len(data)} bytes).",flush=True)
        return 2
    probe=subprocess.run(
        ["ffprobe","-v","error","-select_streams","v:0",
         "-show_entries","stream=width,height","-of","csv=p=0",
         "-i","pipe:0"],input=data,capture_output=True,timeout=30)
    dims=probe.stdout.decode("utf-8","replace").strip()
    print(f"Current feed GIF dimensions: {dims}; bytes={len(data)}",flush=True)
    if probe.returncode or dims!="1024,1024":
        print("FAIL: This current GIF is NOT native 1024. Keep 512 running.",flush=True)
        return 2
    print("PASS: Current near-realtime C3 GIF is genuine 1024x1024.",flush=True)
    return 0


if __name__=="__main__":
    sys.exit(main())
