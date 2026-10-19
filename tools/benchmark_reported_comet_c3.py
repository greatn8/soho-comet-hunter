#!/usr/bin/env python3
"""Offline independent C3 reported-comet positional replay (26 Nov 2024).

Source: Sungrazer published report report_rp_20241126020425,
Robert Pickard, Kreutz C3 "Potential Comet", 2024-11-26:
https://sungrazer.nrl.navy.mil/index.php/reports?order=title&page=448&sort=asc

These are independent *reported* positions, not adjudicated comet identity,
not an official designated comet orbit, and not proof they correspond to the
dashboard's "SOHO-5143" record. We compare both possible vertical origins:
the report page listing does not identify the contributor's image origin.
Astronomical discovery requires confirmation by the Sungrazer team.

No downloads. Requires already-recovered original C3 1024 JPEGs. Plan-only
by default; --execute runs the named EXPERIMENTAL binary on linked images
and saves logs/results. No live service changes or detector replacement.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import subprocess
import tempfile
import time
from pathlib import Path

from benchmark_known_comets import (
    UTC, busy_cuda, controls, index_images, parse_output, pick_frames,
    verify_dimensions,
)

REPORT_ID = "report_rp_20241126020425"
EVENT_ID = "EV20241126T0630_ED2B9D"
REPORT_URL = ("https://sungrazer.nrl.navy.mil/index.php/reports?"
              "order=title&page=448&sort=asc")
# The report explicitly lists these five time, x, y combinations.
RAW_POINTS = (
    ("2024-11-26T05:30:00Z",670.0,910.0),
    ("2024-11-26T05:42:00Z",669.0,908.0),
    ("2024-11-26T05:54:00Z",668.0,907.0),
    ("2024-11-26T06:06:00Z",667.0,906.0),
    ("2024-11-26T06:18:00Z",666.0,904.0),
)


def truth_positions(origin, size=1024):
    if origin not in ("upper_left","lower_left"):
        raise ValueError("Origin must be upper_left or lower_left")
    points=[]
    for timestamp,x,y in RAW_POINTS:
        t=dt.datetime.fromisoformat(timestamp.replace("Z","+00:00"))
        points.append((t,x,y if origin=="upper_left" else size-y))
    return points


def compare_track(track, truth, size=1024):
    """Endpoint-model consistency ONLY; actual image-pixel verification remains required."""
    a,b=track["first"],track["last"]
    duration=(b["time"]-a["time"]).total_seconds()
    if duration<=0 or track["frames"]<5:
        return None
    errors=[]
    for t,x,y in truth:
        if not (a["time"]<=t<=b["time"]):
            continue
        f=(t-a["time"]).total_seconds()/duration
        xp=a["x"]+(b["x"]-a["x"])*f
        yp=a["y"]+(b["y"]-a["y"])*f
        errors.append(math.hypot(xp-x,yp-y))
    if len(errors)<4:
        return None
    errors.sort()
    return {
        "id":track["id"],"frames":track["frames"],
        "reference_points_in_time_window":len(errors),
        "median_error_px":round(errors[len(errors)//2],2),
        "max_error_px":round(max(errors),2),
        "first_time":a["time"].isoformat(),"last_time":b["time"].isoformat(),
        "first_xy":[a["x"],a["y"]],"last_xy":[b["x"],b["y"]],
        "priority":track["priority"],"score":track["score"],
    }


def rank_against_report(tracks):
    summary={}
    for origin in ("upper_left","lower_left"):
        raw=[compare_track(row,truth_positions(origin)) for row in tracks]
        ranked=sorted((r for r in raw if r is not None),
                      key=lambda r:(r["max_error_px"],r["median_error_px"]))
        credible=[r for r in ranked
                  if r["max_error_px"]<=8 and r["median_error_px"]<=6]
        summary[origin]={
            "assumed_origin":origin,
            "tracks_covering_at_least_four_reference_times":len(ranked),
            "provisional_endpoint_matches":credible[:20],
            "best_five_endpoint_models":ranked[:5],
            "note":"Endpoint-model proximity is not a pixel-level comet detection."
        }
    return summary


def run_case(binary, project, frames, report_dir, max_candidates,
             budget_minutes, use_filter):
    # Distinct timestamped original JPEG data is linked, NEVER copied, resized
    # or written to. The temporary staging folder is disposed afterward.
    base="/dev/shm" if Path("/dev/shm").is_dir() else None
    with tempfile.TemporaryDirectory(prefix="soho_reported_c3_",dir=base) as tmp:
        stage=Path(tmp)
        for image in frames:
            dest=stage/image.name
            if dest.exists():
                raise ValueError("Duplicate frame filename")
            try:
                os.link(image,dest)
            except OSError:
                dest.symlink_to(image.resolve())
        args=[str(binary),"--camera","c3","--resolution","1024",
              "--input-dir",str(stage),"--budget-minutes",str(budget_minutes),
              "--min-frames","5","--max-candidates",str(max_candidates)]
        if use_filter:
            args.append("--within-window-stationary-filter")
        proc=subprocess.run(args,cwd=project,capture_output=True,text=True,
                            timeout=max(300,budget_minutes*120))
        logname=("real_20241126_filtered" if use_filter else
                 "real_20241126_unfiltered")+".log"
        raw=proc.stdout+"\n"+proc.stderr
        (report_dir/logname).write_text(raw,encoding="utf-8")
        tracks=parse_output(raw) if proc.returncode==0 else []
        return {
            "filter_enabled":use_filter,"returncode":proc.returncode,
            "log_file":logname,"tracks":len(tracks),
            "output_cap_reached":len(tracks)>=max_candidates,
            "stationary_removed_per_pass":[
                int(line.split(":")[-1].split()[0])
                for line in raw.splitlines()
                if "within-window stationary detections removed:" in line],
            "report_comparison":rank_against_report(tracks),
        }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project",required=True,type=Path)
    p.add_argument("--binary",required=True,type=Path)
    p.add_argument("--frames-root",type=Path)
    p.add_argument("--max-candidates",type=int,default=500)
    p.add_argument("--budget-minutes",type=int,default=1)
    p.add_argument("--wait-for-gpu-seconds",type=int,default=900)
    p.add_argument("--report-dir",type=Path)
    p.add_argument("--execute",action="store_true")
    opts=p.parse_args()
    if not 10<=opts.max_candidates<=1000:
        p.error("max-candidates must be 10..1000")
    if not 1<=opts.budget_minutes<=10:
        p.error("budget-minutes must be 1..10")
    project=opts.project.expanduser().resolve()
    binary=opts.binary.expanduser().resolve()
    cases=[c for c in controls(Path(__file__).resolve().parents[1]/
                                "docs/data/candidates.json") if c["event_id"]==EVENT_ID]
    if len(cases)!=1:
        raise SystemExit("Missing historical dashboard event: "+EVENT_ID)
    case=cases[0]
    root=(opts.frames_root.expanduser().resolve() if opts.frames_root else
          project/"results"/"known_comet_inputs"/EVENT_ID)
    indexed,_,_,_=index_images([root])
    frames=pick_frames(indexed[1024],case["first"],case["last"])
    print("Independent Sungrazer reported C3 positions:", REPORT_ID,flush=True)
    print("Report is Potential Comet, not a confirmed identification.",flush=True)
    print("Historical dashboard control:",EVENT_ID,flush=True)
    print(f"Original 1024px frames: {len(frames)} ({root})",flush=True)
    print("Report anchors (upper-left assumption):",
          [(str(t),x,y) for t,x,y in truth_positions("upper_left")],flush=True)
    print("Also checking the lower-left origin interpretation.",flush=True)
    if len(frames)<5:
        print("INSUFFICIENT ORIGINAL C3 INPUTS. "
              "Use the existing rate-limited recover_known_comet_frames.py "
              "for this event; do not invent frames.",flush=True)
        return 2
    if verify_dimensions(frames[0],1024)!="ok":
        raise SystemExit("Original 1024 C3 JPEG dimension verification failed")
    if not opts.execute:
        print("PLAN ONLY: supply --execute for independent positional replay.",flush=True)
        return 0
    if not binary.is_file():
        raise SystemExit("Experimental CUDA binary missing: "+str(binary))
    report_dir=opts.report_dir or (project/"results"/"real_report_c3_replay"/
                 dt.datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    report_dir.mkdir(parents=True,exist_ok=True)
    report={"event_id":EVENT_ID,"independent_report_id":REPORT_ID,
            "independent_report_url":REPORT_URL,
            "report_class":"Potential Comet (unconfirmed here)",
            "source_frames":len(frames),
            "original_jpegs":True,
            "binary":str(binary),
            "runs":[]}
    for enabled in (False,True):
        deadline=time.monotonic()+opts.wait_for_gpu_seconds
        while busy_cuda() and time.monotonic()<deadline:
            time.sleep(10)
        if busy_cuda():
            row={"filter_enabled":enabled,"status":"GPU_BUSY"}
        else:
            try:
                row=run_case(binary,project,frames,report_dir,
                             opts.max_candidates,opts.budget_minutes,enabled)
                row["status"]="COMPLETE" if row["returncode"]==0 else "CUDA_FAILED"
            except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:
                row={"filter_enabled":enabled,"status":"ERROR","error":str(exc)}
        report["runs"].append(row)
        print(f"\n=== {'FILTER ON' if enabled else 'FILTER OFF'} ===",flush=True)
        print("Status:",row.get("status"),"tracks:",row.get("tracks"),flush=True)
        for origin,rec in row.get("report_comparison",{}).items():
            print(f"  {origin}: provisional_matches="
                  f"{len(rec['provisional_endpoint_matches'])} "
                  f"best={rec['best_five_endpoint_models'][:1]}",flush=True)
        (report_dir/"report.json").write_text(json.dumps(report,indent=2)+"\n")
    print("Result:",report_dir/"report.json",flush=True)
    print("Important: candidate endpoint agreement is not proof of comet identity "
          "or of detection at each reference image.",flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
