#!/usr/bin/env python3
"""Offline A/B test of extra CUDA false-track gates on cached REAL SOHO C3.

NO SOHO fetches, NO production binary replacement, NO realtime state writes.
Compiles a *separate* experimental binary and runs 12h cache with/without
new gates. Runtime defaults to 1 minute per variant. Avoid running while
realtime CUDA job uses GPU; inspect processes first.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

STAMP = re.compile(r"^(\d{8}_\d{4})_c3_512\.jpg$")
TRACK = re.compile(r"^C\d+\s+\[.+?\].*?\|\s*frames=(\d+).*?\|\s*RMS=([0-9.]+)")
WARN = re.compile(r"integrity reject:\s*(\d+)\s*\|\s*path-null tested:\s*(\d+)\s*\|\s*path-null rejected:\s*(\d+)")
TOTAL = re.compile(r"^Total retained:\s*(\d+)")


def read_tracks(log):
    return [
        {"frames": int(m.group(1)), "rms": float(m.group(2))}
        for line in log.splitlines()
        if (m := TRACK.search(line))
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--source", type=Path,
                        default=Path(__file__).resolve().parents[1] / "cuda" / "comet_hunter_archive.cu")
    parser.add_argument("--hours", type=int, default=12)
    parser.add_argument("--budget-minutes", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.hours < 2 or args.hours > 336 or args.budget_minutes < 1 or args.budget_minutes > 10:
        parser.error("hours must be 2..336; budget 1..10 minutes")
    source = args.source.resolve()
    if not source.is_file():
        parser.error(f"CUDA source missing: {source}")

    cache = Path("/dev/shm") / f"comet_realtime_{os.getlogin()}_c3_512"
    if not cache.exists():
        parser.error(f"Realtime image cache not found: {cache}")

    available = []
    for path in cache.glob("*_c3_512.jpg"):
        match = STAMP.match(path.name)
        if not match:
            continue
        timestamp = dt.datetime.strptime(match.group(1), "%Y%m%d_%H%M")
        if path.stat().st_size > 10000:
            available.append((timestamp, path))
    if not available:
        parser.error("No C3 frames cached")
    available.sort()
    newest = available[-1][0]
    selected = [
        p for t, p in available
        if (newest - t).total_seconds() <= args.hours * 3600
    ]
    if len(selected) < 6:
        parser.error(f"Only {len(selected)} frames in {args.hours}h window")
    output = (args.output or (args.project / "results" / "trajectory_gate_ab")).resolve()
    output.mkdir(parents=True, exist_ok=True)
    binary = output / "comet_hunter_trajectory_experiment"
    cmd = ["nvcc", "-O3", "-std=c++17", "-arch=sm_80", "--use_fast_math",
           str(source), "-o", str(binary), "-lnvjpeg"]
    print("Building isolated CUDA test binary:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)
    variants = {
        "current": ["--within-window-stationary-filter"],
        "trajectory_integrity": [
            "--within-window-stationary-filter",
            "--trajectory-integrity-filter",
        ],
        "trajectory_integrity_plus_null": [
            "--within-window-stationary-filter",
            "--trajectory-integrity-filter",
            "--trajectory-null-control",
        ],
    }
    report = []
    with tempfile.TemporaryDirectory(prefix="soho_trajectory_ab_") as folder:
        stage = Path(folder)
        for image in selected:
            target = stage / image.name
            try:
                os.link(image, target)
            except OSError:
                target.symlink_to(image)
        for label, flags in variants.items():
            print(f"\nRUNNING {label} ({len(selected)} real cached images)", flush=True)
            command = [str(binary), "--camera", "c3", "--resolution", "512",
                       "--input-dir", str(stage),
                       "--budget-minutes", str(args.budget_minutes),
                       "--min-frames", "5", "--max-candidates", "100"] + flags
            start = time.monotonic()
            proc = subprocess.run(command, cwd=args.project, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            (output / f"{label}.log").write_text(proc.stdout)
            tracks = read_tracks(proc.stdout)
            integrity = [tuple(map(int, m.groups())) for m in WARN.finditer(proc.stdout)]
            total = [int(m.group(1)) for line in proc.stdout.splitlines()
                     if (m := TOTAL.search(line))]
            item = {
                "variant": label, "exit_code": proc.returncode,
                "elapsed_seconds": round(time.monotonic() - start, 1),
                "reported_tracks": len(tracks),
                "last_reported_total": total[-1] if total else None,
                "reported_max_frames": max([t["frames"] for t in tracks], default=0),
                "integrity_rejected_across_passes": sum(v[0] for v in integrity),
                "null_tested_across_passes": sum(v[1] for v in integrity),
                "null_rejected_across_passes": sum(v[2] for v in integrity),
                "log": str(output / f"{label}.log"),
            }
            report.append(item)
            print(json.dumps(item, indent=2), flush=True)
            if proc.returncode != 0:
                break
    result = {"source": str(source), "last_frame": selected[-1].name,
              "frames": len(selected), "window_hours": args.hours, "runs": report,
              "warning": "Track count reduction alone does NOT establish comet recall. Do not deploy until positive controls pass."}
    (output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    print("\nA/B report:", output / "report.json")
    if not report or any(r["exit_code"] != 0 for r in report):
        raise SystemExit("A/B failed. Review per-variant logs; production untouched.")


if __name__ == "__main__":
    main()
