#!/usr/bin/env python3
"""Experimental offline spatial CUDA replay over original SOHO C3 images.

CUDA's full-frame top-N lists can be dominated by a single image region. This
experiment repeats detection on overlapping regions without moving any pixels
or changing image coordinates. Outside-region pixels are BLACKED OUT in
temporary, explicitly derived input frames. These are NOT original imagery.

By default it only prints the plan. --execute uses original images already
downloaded on Bourbaki. It makes no SOHO requests and never writes the live
realtime cache or detector state.

The experiment does NOT claim comet recoveries without independently verified
reference coordinates. A successful spatial scan is only evidence that CUDA
can explore an otherwise underrepresented area.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

from benchmark_known_comets import (
    UTC, busy_cuda, controls, index_images, parse_output, pick_frames,
    save_report, verify_dimensions,
)


def region_bounds(size, grid, region_index, overlap):
    if grid < 2 or size % grid:
        raise ValueError("Image dimensions must be divisible by grid >=2")
    if region_index < 0 or region_index >= grid*grid:
        raise ValueError("Region index outside grid")
    if overlap < 0 or overlap >= size//grid:
        raise ValueError("Overlap must be smaller than a grid cell")
    row, column = divmod(region_index, grid)
    cell = size//grid
    return (
        max(0,column*cell-overlap),
        max(0,row*cell-overlap),
        min(size,(column+1)*cell+overlap),
        min(size,(row+1)*cell+overlap),
    )


def mask_filter(size, bounds):
    """Draw only outside the ROI. Output pixels retain original positions."""
    x0,y0,x1,y1 = bounds
    regions = (
        (0,0,size,y0),
        (0,y1,size,size-y1),
        (0,y0,x0,y1-y0),
        (x1,y0,size-x1,y1-y0),
    )
    boxes = []
    for x,y,w,h in regions:
        if w>0 and h>0:
            boxes.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color=black:t=fill")
    return ",".join(boxes) or "null"


def encode_masked(source, destination, size, bounds):
    binary = shutil.which("ffmpeg")
    if not binary:
        raise RuntimeError("ffmpeg is required for spatial masking")
    args = [
        binary,"-hide_banner","-loglevel","error","-nostdin","-y",
        "-i",str(source),"-vf",mask_filter(size,bounds),
        "-frames:v","1","-q:v","2",str(destination),
    ]
    proc = subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          timeout=60)
    if proc.returncode:
        raise RuntimeError(f"ffmpeg mask failed for {source}: "
                           f"{proc.stderr.decode('utf-8','replace')[-500:]}")
    if verify_dimensions(destination,size) != "ok":
        raise RuntimeError("Spatially masked test frame changed resolution")


def run_cuda_region(binary, project, frames, resolution, bounds,
                    budget_minutes, max_candidates, log_path, control="real"):
    base = "/dev/shm" if Path("/dev/shm").is_dir() else None
    with tempfile.TemporaryDirectory(prefix="soho_spatial_test_",dir=base) as name:
        inputs = Path(name)
        if control not in ("real","static"):
            raise ValueError("Unknown input control: "+control)
        for source in frames:
            # The static negative control repeats precisely the SAME image
            # pixels at every timestamp. No real object can move in it.
            # Filenames/cadence stay identical to the real observation.
            image_source = frames[0] if control=="static" else source
            encode_masked(image_source,inputs/source.name,resolution,bounds)
        cmd = [
            str(binary),"--camera","c3","--resolution",str(resolution),
            "--input-dir",str(inputs),
            "--budget-minutes",str(budget_minutes),
            "--min-frames","5",
            "--max-candidates",str(max_candidates),
        ]
        proc=subprocess.run(cmd,cwd=project,text=True,capture_output=True,
                            timeout=max(240, budget_minutes*120))
        output = proc.stdout + "\n" + proc.stderr
        log_path.write_text(output,encoding="utf-8")
        return proc.returncode,parse_output(output)


def summarize_tracks(tracks, bounds, source_frames, max_candidates):
    x0,y0,x1,y1 = bounds
    midpoint_inside = []
    outside = 0
    for track in tracks:
        first,last = track["first"],track["last"]
        xm=(first["x"]+last["x"])/2
        ym=(first["y"]+last["y"])/2
        if x0 <= xm < x1 and y0 <= ym < y1:
            midpoint_inside.append(track)
        else:
            outside += 1
    def valid_geometry(c):
        a,b=c["first"],c["last"]
        span=(b["time"]-a["time"]).total_seconds()/3600
        if span<=0 or c["frames"]>source_frames:
            return False
        # This is a geometry sanity check, not independent pixel evidence.
        return 0.2 <= math.dist((a["x"],a["y"]),(b["x"],b["y"]))/span <= 30

    return {
        "tracks_total":len(tracks),
        "cap_reached":len(tracks)>=max_candidates,
        "midpoints_inside_roi":len(midpoint_inside),
        "midpoints_outside_roi":outside,
        "good_fit_rms_le_1_2":sum(c["rms"]<=1.2 for c in midpoint_inside),
        "plausible_basic_geometry":sum(valid_geometry(c) for c in midpoint_inside),
        "first_track_ids":[c["id"] for c in midpoint_inside[:8]],
    }


def select_event(case_list, selected):
    matches = [x for x in case_list if
               selected in x["event_id"] or selected.lower() in x["known_object"].lower()]
    if len(matches)!=1:
        raise ValueError(f"Expected exactly one matching known event, got {len(matches)}")
    return matches[0]


def main():
    parser=argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project",type=Path,required=True)
    parser.add_argument("--case",default="EV20260730T0530_1E9C7C")
    parser.add_argument("--resolution",choices=(512,1024),type=int,default=1024)
    parser.add_argument("--grid",type=int,default=4)
    parser.add_argument("--overlap",type=int,default=48)
    parser.add_argument("--regions",default="all",
                        help="all or comma-separated region IDs, 0-based row-major")
    parser.add_argument("--budget-minutes",type=int,default=1)
    parser.add_argument("--max-candidates",type=int,default=250)
    parser.add_argument("--wait-for-gpu-seconds",type=int,default=900)
    parser.add_argument("--frames-root",type=Path,
                        help="Folder of original, unmodified C3 images")
    parser.add_argument("--report-dir",type=Path)
    parser.add_argument("--control",choices=("real","static"),default="real",
                        help="real=original evolving frames; static=repeat the first input frame at all timestamps")
    parser.add_argument("--execute",action="store_true")
    opts=parser.parse_args()
    project=opts.project.expanduser().resolve()
    if opts.grid<2 or opts.grid>8 or opts.resolution%opts.grid:
        parser.error("--grid must divide the image resolution, between 2 and 8")
    if opts.overlap<0 or opts.overlap>=opts.resolution//opts.grid:
        parser.error("--overlap must be smaller than grid cell width")
    if opts.budget_minutes<1 or opts.budget_minutes>10:
        parser.error("--budget-minutes must be between 1 and 10")
    if opts.max_candidates<10 or opts.max_candidates>1000:
        parser.error("--max-candidates must be 10..1000")

    try:
        regions = (list(range(opts.grid**2)) if opts.regions=="all" else
                   sorted({int(i) for i in opts.regions.split(",")}))
        if not regions or any(i<0 or i>=opts.grid**2 for i in regions):
            raise ValueError("Region IDs outside chosen grid")
    except ValueError as exc:
        parser.error(str(exc))

    case=select_event(controls(Path(__file__).resolve().parents[1] /
                               "docs/data/candidates.json"),opts.case)
    root=(opts.frames_root.expanduser().resolve() if opts.frames_root else
          project/"results"/"known_comet_inputs"/case["event_id"])
    indexed,inspected,_,_=index_images([root])
    frames=pick_frames(indexed[opts.resolution],case["first"],case["last"])
    if len(frames)<5:
        raise SystemExit(f"No sufficient original {opts.resolution}px frames at {root}: {len(frames)}")
    if verify_dimensions(frames[0],opts.resolution)!="ok":
        raise SystemExit("Original source frame dimensions do not match resolution")

    report_dir=opts.report_dir or (project/"results"/"spatial_comet_replay"/
               dt.datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    report={"event_id":case["event_id"],"known_object":case["known_object"],
            "control_mode":opts.control,
            "negative_control_no_real_motion":opts.control=="static",
            "resolution":opts.resolution,"grid":opts.grid,"overlap":opts.overlap,
            "experimental":True,"derived_images_not_original":True,
            "image_count":len(frames),"source_dir":str(root),
            "max_candidates_per_region":opts.max_candidates,"regions":[]}
    print(f'Known control: {case["known_object"]} | {opts.resolution}px '
          f'| original input frames={len(frames)} | grid={opts.grid}x{opts.grid} '
          f'| control={opts.control}',flush=True)
    if opts.control=="static":
        print("NEGATIVE CONTROL: identical original frame repeated with each timestamp. "
              "Every candidate trajectory is spurious motion.",flush=True)
    for i in regions:
        bounds=region_bounds(opts.resolution,opts.grid,i,opts.overlap)
        print(f"Region {i:02d}: keep ROI {bounds}",flush=True)
        report["regions"].append({"region":i,"bounds":bounds,"status":"PLANNED"})
    if not opts.execute:
        print("PLAN ONLY. --execute runs CUDA on temporary MASKED copies, "
              "not the original full-frame benchmark.",flush=True)
        return 0

    binary=project/"comet_hunter_archive"
    if not binary.is_file():
        raise SystemExit(f"CUDA detector missing: {binary}")
    report_dir.mkdir(parents=True,exist_ok=True)
    for entry in report["regions"]:
        idx=entry["region"]
        deadline=time.monotonic()+opts.wait_for_gpu_seconds
        while busy_cuda() and time.monotonic()<deadline:
            time.sleep(10)
        if busy_cuda():
            entry["status"]="GPU_BUSY"
            print(f"Region {idx}: GPU busy, skipped",flush=True)
            continue
        logfile=report_dir/f"region_{idx:02d}_{opts.resolution}_{opts.control}.log"
        try:
            code,tracks=run_cuda_region(binary,project,frames,opts.resolution,
                                       entry["bounds"],opts.budget_minutes,
                                       opts.max_candidates,logfile,control=opts.control)
            entry["status"]="COMPLETE" if code==0 else "CUDA_FAILED"
            entry["returncode"]=code
            entry["log_file"]=logfile.name
            entry.update(summarize_tracks(tracks,entry["bounds"],len(frames),
                                          opts.max_candidates))
            if opts.control=="static":
                entry["known_false_motion_tracks"]=len(tracks)
                entry["interpretation"]="All reported moving tracks are false: the input pixels never change."
            print(f"Region {idx:02d}: status={entry['status']} "
                  f"tracks={entry['tracks_total']} "
                  f"inside={entry['midpoints_inside_roi']} "
                  f"cap={entry['cap_reached']}",flush=True)
        except Exception as exc:
            entry["status"]="ERROR"
            entry["error"]=str(exc)
            print(f"Region {idx:02d}: ERROR {exc}",flush=True)
        (report_dir/"report.json").write_text(json.dumps(report,indent=2)+"\n")
    print("Report:",report_dir/"report.json",flush=True)
    print("Reminder: masked-frame runs are experimental; no confirmed comet "
          "recovery or calibrated sensitivity was measured.",flush=True)


if __name__=="__main__":
    main()
