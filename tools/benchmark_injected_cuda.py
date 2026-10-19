#!/usr/bin/env python3
"""Offline synthetic moving-point recall test of the experimental CUDA tracker.

Uses one recovered original SOHO frame as a stationary background at all
real observation timestamps. The positive condition draws a small moving
bright point with ffmpeg onto disposable copies of that frame; negative
condition repeats the original JPEG bytes unchanged. The moving trajectory
has known reference x/y at every input timestamp.

This is an engineering control, NOT a real comet observation or a measurement
of astronomical recall. No SOHO downloads, source-file writes, or changes to
realtime services. The CUDA binary must be provided explicitly.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from benchmark_known_comets import (
    UTC, busy_cuda, controls, index_images, parse_output, pick_frames,
    verify_dimensions,
)
from benchmark_static_cuda import sha256, stage_images


def truth_positions(frames, start_x, start_y, vx, vy):
    """Reference center of the injected point in original 1024px coordinates."""
    if not frames:
        raise ValueError("No input frames")
    from benchmark_known_comets import when
    start = when(frames[0].name)
    result = []
    for frame in frames:
        t = when(frame.name)
        hours = (t - start).total_seconds() / 3600.0
        # Coordinates are rounded before drawing in the 1024px image.
        result.append({
            "filename": frame.name,
            "utc": t.isoformat(),
            "x": round(start_x + vx * hours),
            "y": round(start_y + vy * hours),
        })
    return result


def match_synthetic_track(track, truth, tolerance_px=6.0):
    """Match two reported endpoints against ground truth at exactly those times.

    This is a *candidate-level* test, not per-frame astronomical validation.
    A reported track needs >= 5 detections, positive time span, and BOTH
    endpoints near the independently chosen injected positions.
    """
    by_time = {row["utc"]: row for row in truth}
    from benchmark_known_comets import when
    first, last = track["first"], track["last"]
    a = by_time.get(when(first["time"]).isoformat())
    b = by_time.get(when(last["time"]).isoformat())
    if not a or not b or track["frames"] < 5 or first["time"] >= last["time"]:
        return None
    ea = math.hypot(first["x"] - a["x"], first["y"] - a["y"])
    eb = math.hypot(last["x"] - b["x"], last["y"] - b["y"])
    if ea <= tolerance_px and eb <= tolerance_px:
        return {"id": track["id"], "frames": track["frames"],
                "first_error_px": round(ea,2), "last_error_px": round(eb,2),
                "rms_px": track["rms"], "score": track["score"]}
    return None


def encode_moving_dot(original, target, x, y, image_size, dot_size):
    """Experimental JPEG with a moving bright source. Original is not changed."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg missing")
    half=dot_size//2
    # Keep a substantial margin beyond the detector's edge mask and for
    # the local background window used to detect point-like bright sources.
    if not (32 <= x-half < x+half < image_size-32 and
            32 <= y-half < y+half < image_size-32):
        raise ValueError("Injection positions must remain well inside the image")
    vf=(f"drawbox=x={x-half}:y={y-half}:w={dot_size}:h={dot_size}:"
        f"color=white:t=fill")
    cmd=["ffmpeg","-hide_banner","-loglevel","error","-nostdin",
         "-y","-i",str(original),"-vf",vf,"-frames:v","1",
         "-q:v","2",str(target)]
    proc=subprocess.run(cmd,capture_output=True,text=True,timeout=45)
    if proc.returncode:
        raise RuntimeError(f"ffmpeg synthetic point failed: {proc.stderr[-400:]}")
    if verify_dimensions(target,image_size)!="ok":
        raise RuntimeError("Synthetic JPEG dimensions unexpectedly changed")


def execute_one(binary, project, frames, truth, size, injected, filtered,
                max_candidates, budget_minutes, report_dir):
    """Stage all temporary inputs in /dev/shm then run CUDA once."""
    temp_base="/dev/shm" if Path("/dev/shm").is_dir() else None
    with tempfile.TemporaryDirectory(prefix="soho_moving_point_",dir=temp_base) as tmp:
        stage=Path(tmp)
        if injected:
            for frame,row in zip(frames,truth):
                encode_moving_dot(frames[0],stage/frame.name,
                                  row["x"],row["y"],size,3)
        else:
            stage_images(frames,stage,"static")
        if len(list(stage.glob("*.jpg"))) != len(frames):
            raise RuntimeError("Input staging incomplete")
        hashes=[sha256(stage/x.name) for x in frames]
        if not injected and len(set(hashes))!=1:
            raise RuntimeError("Static negative-control frames differ")
        if injected and len(set(hashes))<2:
            raise RuntimeError("Moving-point frames are unexpectedly identical")

        args=[str(binary),"--camera","c3","--resolution",str(size),
              "--input-dir",str(stage), "--budget-minutes",str(budget_minutes),
              "--min-frames","5","--max-candidates",str(max_candidates)]
        if filtered:
            args.append("--within-window-stationary-filter")
        proc=subprocess.run(args,cwd=project,capture_output=True,text=True,
                            timeout=max(300,budget_minutes*120))
        output=proc.stdout+"\n"+proc.stderr
        label=("injected" if injected else "static")+("_filtered" if filtered else "_unfiltered")
        log=report_dir/f"{label}_{size}.log"
        log.write_text(output,encoding="utf-8")
        tracks=parse_output(output) if proc.returncode==0 else []
        matches=[m for track in tracks
                 if (m:=match_synthetic_track(track,truth)) is not None] if injected else []
        return {
            "variant":label,
            "cuda_returncode":proc.returncode,
            "log_file":log.name,
            "track_count":len(tracks),
            "candidate_cap_hit":len(tracks)>=max_candidates,
            "synthetic_endpoint_matches":matches[:15],
            "synthetic_recovered_at_endpoints":bool(matches) if injected else None,
            "all_reported_tracks_known_false":not injected,
            "input_files_byte_identical":len(set(hashes))==1,
            "stationary_removed_per_pass":[
                int(line.split(":")[-1].split()[0])
                for line in output.splitlines()
                if "within-window stationary detections removed:" in line],
        }


def main():
    p=argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project",type=Path,required=True)
    p.add_argument("--binary",type=Path,required=True,
                   help="Separate experimental CUDA binary; NEVER overwrite production")
    p.add_argument("--case",default="EV20260730T0530_1E9C7C")
    p.add_argument("--resolution",type=int,choices=(1024,),default=1024)
    p.add_argument("--frames-root",type=Path)
    p.add_argument("--x",type=float,default=620.0)
    p.add_argument("--y",type=float,default=680.0)
    p.add_argument("--vx",type=float,default=8.0,help="pixels per hour")
    p.add_argument("--vy",type=float,default=-6.0,help="pixels per hour")
    p.add_argument("--max-candidates",type=int,default=250)
    p.add_argument("--budget-minutes",type=int,default=1)
    p.add_argument("--wait-for-gpu-seconds",type=int,default=900)
    p.add_argument("--report-dir",type=Path)
    p.add_argument("--execute",action="store_true")
    a=p.parse_args()
    if not 10<=a.max_candidates<=1000 or not 1<=a.budget_minutes<=10:
        p.error("max candidates 10..1000; budget minutes 1..10")
    if math.hypot(a.vx,a.vy)<1 or max(abs(a.vx),abs(a.vy))>12:
        p.error("Inject a detectable trajectory at speed 1..12 px/h per component")
    project=a.project.expanduser().resolve()
    binary=a.binary.expanduser().resolve()
    choices=[c for c in controls(Path(__file__).resolve().parents[1]/
                                 "docs/data/candidates.json") if c["event_id"]==a.case]
    if len(choices)!=1:
        p.error("No exactly matching control event")
    case=choices[0]
    root=(a.frames_root.expanduser().resolve() if a.frames_root else
          project/"results"/"known_comet_inputs"/case["event_id"])
    indexed,_,_,_=index_images([root])
    frames=pick_frames(indexed[a.resolution],case["first"],case["last"])
    if len(frames)<8 or verify_dimensions(frames[0],a.resolution)!="ok":
        raise SystemExit("Need at least eight original JPEG inputs of requested resolution")
    truth=truth_positions(frames,a.x,a.y,a.vx,a.vy)
    for r in truth:
        if not (34<=r["x"]<a.resolution-34 and 34<=r["y"]<a.resolution-34):
            p.error("Injected trajectory would leave the safe interior")
    print(f"Original inputs: {len(frames)}; synthetic point: ({a.x},{a.y}), "
          f"velocity ({a.vx},{a.vy}) px/h",flush=True)
    print("Static negative vs synthetic moving-point positive, filter enabled.",flush=True)
    if not a.execute:
        print("PLAN ONLY: add --execute to run with disposable JPEGs.",flush=True)
        return
    if not binary.is_file():
        raise SystemExit("Experimental CUDA binary unavailable: "+str(binary))
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is required")
    report_dir=(a.report_dir or project/"results"/"injected_cuda_control"/
                dt.datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    report_dir.mkdir(parents=True,exist_ok=True)
    report={"synthetic_control":True,"independent_astronomical_truth":False,
            "case":a.case,"source_file_sha256":sha256(frames[0]),
            "detector_binary":str(binary),"reference_trajectory":truth,
            "runs":[]}
    for injected in (False,True):
        until=time.monotonic()+a.wait_for_gpu_seconds
        while busy_cuda() and time.monotonic()<until:
            time.sleep(10)
        if busy_cuda():
            row={"variant":"injected" if injected else "static","status":"GPU_BUSY"}
        else:
            try:
                row=execute_one(binary,project,frames,truth,a.resolution,
                                injected,True,a.max_candidates,a.budget_minutes,
                                report_dir)
                row["status"]="COMPLETE" if row["cuda_returncode"]==0 else "CUDA_FAILED"
            except (OSError,RuntimeError,ValueError,subprocess.TimeoutExpired) as ex:
                row={"variant":"injected" if injected else "static",
                     "status":"ERROR","error":str(ex)}
        report["runs"].append(row)
        print(json.dumps(row,indent=2),flush=True)
        (report_dir/"report.json").write_text(json.dumps(report,indent=2)+"\n")
    print("Report:",report_dir/"report.json",flush=True)
    print("No real comet recovery is established by this synthetic test.",flush=True)


if __name__=="__main__":
    main()
