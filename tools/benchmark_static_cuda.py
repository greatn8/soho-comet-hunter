#!/usr/bin/env python3
"""Unmasked static-scene control for the original SOHO CUDA detector.

This experiment tests whether the CUDA trajectory linker invents motion when
every frame contains exactly the same JPEG bytes. It stages 23 (or however
many are present) different timestamped filenames pointing to ONE original
image, without conversion, masking, JPEG recompression, or SOHO access.

The control does not alter the original frames, live cache, realtime hunter,
CUDA binary or dashboard. All moving tracks in a static run are false.

Usage:
  python3 tools/benchmark_static_cuda.py --project ~/comethunting/comet_hunter_native_cuda_v7_archive --execute
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from benchmark_known_comets import (
    UTC, busy_cuda, controls, index_images, parse_output, pick_frames,
    verify_dimensions,
)


def stage_images(frames, stage, mode):
    """Preserve timestamped filenames while using identical image bytes in static mode."""
    if mode not in ("static", "real"):
        raise ValueError("Unknown mode: " + mode)
    staged = []
    for frame in frames:
        source = frames[0] if mode == "static" else frame
        target = stage / frame.name
        if target.exists() or target.is_symlink():
            raise ValueError("Duplicate image timestamp: " + frame.name)
        try:
            os.link(source, target)
        except OSError:
            target.symlink_to(source.resolve())
        staged.append(target)
    return staged


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def summarize(output, tracks, count, limit, mode):
    reported = sum(line.lstrip().startswith("C") and " | pass=" in line
                   for line in output.splitlines())
    # Endpoints are only metadata: none can constitute genuine motion in a
    # static sequence, regardless of number of detections or fit score.
    spans = []
    impossible_counts = 0
    for track in tracks:
        duration=(track["last"]["time"]-track["first"]["time"]).total_seconds()/3600
        displacement=math.hypot(track["last"]["x"]-track["first"]["x"],
                                track["last"]["y"]-track["first"]["y"])
        if duration>0:
            spans.append(round(displacement,2))
        if track["frames"]>count:
            impossible_counts+=1
    return {
        "mode":mode,
        "frames":count,
        "cuda_track_headers":reported,
        "parsed_tracks":len(tracks),
        "parse_loss":max(0,reported-len(tracks)),
        "candidate_cap_reached":len(tracks)>=limit,
        "impossible_frame_counts":impossible_counts,
        "moving_endpoint_tracks":sum(value>1 for value in spans),
        "median_endpoint_displacement_pixels":(
            sorted(spans)[len(spans)//2] if spans else None),
        "definitely_false_moving_tracks":(
            sum(value>1 for value in spans) if mode=="static" else None),
        "interpretation":(
            "NO_TRUE_MOTION_POSSIBLE" if mode=="static" else "REAL_IMAGES_UNVERIFIED"
        ),
    }


def run_once(binary, project, frames, resolution, mode, budget, limit, report_dir):
    base = "/dev/shm" if Path("/dev/shm").is_dir() else None
    with tempfile.TemporaryDirectory(prefix="soho_unmasked_static_", dir=base) as path:
        stage = Path(path)
        selected=stage_images(frames,stage,mode)
        if len(selected)!=len(frames):
            raise RuntimeError("Staging lost an image")
        # Validate that two different timestamped inputs have the exact same
        # content before drawing conclusions about false motion.
        if mode=="static" and len(selected)>1 and sha256(selected[0])!=sha256(selected[-1]):
            raise RuntimeError("Static image bytes differ; negative control invalid")
        cmd=[str(binary),"--camera","c3","--resolution",str(resolution),
             "--input-dir",str(stage),"--budget-minutes",str(budget),
             "--min-frames","5","--max-candidates",str(limit)]
        proc=subprocess.run(cmd,cwd=project,text=True,capture_output=True,
                            timeout=max(300,budget*120))
        output=proc.stdout+"\n"+proc.stderr
        log=report_dir/f"unmasked_{mode}_{resolution}.log"
        log.write_text(output,encoding="utf-8")
        return proc.returncode, output, log.name


def main():
    ap=argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project",type=Path,required=True)
    ap.add_argument("--case",default="EV20260730T0530_1E9C7C")
    ap.add_argument("--resolution",choices=(512,1024),type=int,default=1024)
    ap.add_argument("--frames-root",type=Path)
    ap.add_argument("--max-candidates",type=int,default=250)
    ap.add_argument("--budget-minutes",type=int,default=1)
    ap.add_argument("--wait-for-gpu-seconds",type=int,default=900)
    ap.add_argument("--compare-real",action="store_true",
                    help="Also run the ordinary unmodified evolving image sequence")
    ap.add_argument("--report-dir",type=Path)
    ap.add_argument("--execute",action="store_true",
                    help="Run CUDA; without this option only print the test plan")
    args=ap.parse_args()
    if not 10<=args.max_candidates<=1000:
        ap.error("--max-candidates must be 10..1000")
    if not 1<=args.budget_minutes<=10:
        ap.error("--budget-minutes must be 1..10")
    project=args.project.expanduser().resolve()
    cases=[c for c in controls(Path(__file__).resolve().parents[1]/
                               "docs/data/candidates.json")
           if args.case in c["event_id"] or args.case.lower() in c["known_object"].lower()]
    if len(cases)!=1:
        ap.error("Known-comet case must match exactly one record")
    case=cases[0]
    images=(args.frames_root.expanduser().resolve() if args.frames_root else
            project/"results"/"known_comet_inputs"/case["event_id"])
    indexed,_,_,_=index_images([images])
    frames=pick_frames(indexed[args.resolution],case["first"],case["last"])
    if len(frames)<5:
        raise SystemExit(f"Only {len(frames)} original frames for {case['event_id']}")
    if verify_dimensions(frames[0],args.resolution)!="ok":
        raise SystemExit("Original image dimensions invalid")
    first_image_sha=sha256(frames[0])
    report_dir=(args.report_dir or (project/"results"/"static_cuda_control"/
                dt.datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")))
    modes=["static","real"] if args.compare_real else ["static"]
    print(f"Positive reference: {case['known_object']} | {args.resolution}px",flush=True)
    print(f"Input images: {len(frames)} | first source sha256: {first_image_sha}",flush=True)
    print("Modes: "+", ".join(modes),flush=True)
    print("STATIC: exactly the same original JPEG bytes under each original timestamp.",flush=True)
    if not args.execute:
        print("PLAN ONLY: --execute to run the unmasked negative control.",flush=True)
        return
    binary=project/"comet_hunter_archive"
    if not binary.is_file():
        raise SystemExit(f"CUDA detector missing: {binary}")
    report_dir.mkdir(parents=True,exist_ok=True)
    result={"control":case["known_object"],"event_id":case["event_id"],
            "original_images":len(frames),"resolution":args.resolution,
            "first_image_sha256":first_image_sha,"unmasked":True,
            "source_images_unchanged":True,"runs":[]}
    for mode in modes:
        until=time.monotonic()+args.wait_for_gpu_seconds
        while busy_cuda() and time.monotonic()<until:
            time.sleep(10)
        if busy_cuda():
            entry={"mode":mode,"status":"GPU_BUSY"}
        else:
            try:
                code,output,filename=run_once(
                    binary,project,frames,args.resolution,mode,
                    args.budget_minutes,args.max_candidates,report_dir)
                tracks=parse_output(output)
                entry=summarize(output,tracks,len(frames),args.max_candidates,mode)
                entry.update({"cuda_return_code":code,
                              "status":"COMPLETE" if code==0 else "CUDA_FAILED",
                              "log_file":filename})
            except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:
                entry={"mode":mode,"status":"ERROR","error":str(exc)}
        result["runs"].append(entry)
        print(json.dumps(entry,indent=2),flush=True)
        (report_dir/"report.json").write_text(json.dumps(result,indent=2)+"\n")
    print("Report:",report_dir/"report.json",flush=True)
    print("This experiment makes no positive comet discovery claim.",flush=True)


if __name__=="__main__":
    main()
