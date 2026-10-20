#!/usr/bin/env python3
"""Offline A/B replay of published known-object C3 intervals with CUDA gates.

This is NOT evidence that any track belongs to a known comet unless independent
position measurements are supplied. Replays original cached 512 and 1024 JPEGs
without network requests or modifying production. Requires an idle GPU.
"""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from benchmark_known_comets import (UTC, busy_cuda, controls, image_roots,
    index_images, parse_output, pick_frames, positional_errors, when,
    verify_dimensions)

VARIANTS = {
    "current": ["--within-window-stationary-filter"],
    "integrity": ["--within-window-stationary-filter", "--trajectory-integrity-filter"],
    "integrity_null": ["--within-window-stationary-filter", "--trajectory-integrity-filter",
                       "--trajectory-null-control"],
}


def load_truth(path, event_id):
    if not path:
        return []
    rows=[]
    with Path(path).open(newline="",encoding="utf-8") as file:
        reader=csv.DictReader(file)
        expected={"event_id","utc","x1024","y1024","origin"}
        if not expected.issubset(reader.fieldnames or []):
            raise ValueError("Independent truth CSV requires event_id,utc,x1024,y1024,origin")
        for row in reader:
            if row["event_id"]!=event_id:
                continue
            origin=row["origin"].strip().lower()
            if origin not in ("lower_left","upper_left"):
                raise ValueError("origin must be lower_left or upper_left")
            x,y=float(row["x1024"]),float(row["y1024"])
            if not (0<=x<=1024 and 0<=y<=1024):
                raise ValueError("truth point outside original 1024 image")
            if origin=="lower_left":
                y=1024-y
            rows.append((when(row["utc"]),x,y))
    return sorted(rows)


def assess(tracks, case, resolution, truth, tolerance_px=20):
    temporal = [t for t in tracks if
                t["frames"] >= 5 and
                min(t["last"]["time"], case["last"]) >=
                max(t["first"]["time"], case["first"])]
    matches = []
    for t in temporal:
        errors = positional_errors(t, truth, resolution) if truth else []
        if len(errors) >= 3 and errors[len(errors)//2] <= tolerance_px:
            matches.append({"track": t["id"], "median_error_1024": round(errors[len(errors)//2],2)})
    matches.sort(key=lambda x:x["median_error_1024"])
    return {
        "returned_tracks": len(tracks), "candidate_cap_hit": len(tracks)>=500,
        "temporally_overlapping_tracks": len(temporal),
        "independent_truth_points":len(truth),
        "positional_recall":bool(matches) if len(truth)>=3 else None,
        "positional_matches":matches[:10],
        "warning":"Temporal overlap is not comet recovery" if len(truth)<3 else None
    }


def run_one(binary,project,frames,size,minutes,cap,flags,logfile):
    with tempfile.TemporaryDirectory(prefix="soho_known_comet_gate_",dir="/dev/shm") as tmp:
        stage=Path(tmp)
        for src in frames:
            dst=stage/src.name
            try: os.link(src,dst)
            except OSError: dst.symlink_to(src.resolve())
        command=[str(binary),"--camera","c3","--resolution",str(size),
                 "--input-dir",str(stage),"--budget-minutes",str(minutes),
                 "--min-frames","5","--max-candidates",str(cap)]+flags
        process=subprocess.run(command,cwd=project,capture_output=True,text=True,
                               timeout=max(300,minutes*180))
        output=process.stdout+"\n"+process.stderr
        logfile.write_text(output)
        return process.returncode,parse_output(output)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project",type=Path,required=True)
    p.add_argument("--binary",type=Path,help="Previously built experimental binary. Defaults to results/trajectory_gate_ab")
    p.add_argument("--truth",type=Path,help="CSV columns event_id,utc,x1024,y1024,origin; independently sourced")
    p.add_argument("--event-id",action="append",help="Select control; repeat for several")
    p.add_argument("--frames-root",type=Path,action="append",default=[])
    p.add_argument("--minutes",type=int,default=2)
    p.add_argument("--max-candidates",type=int,default=500)
    p.add_argument("--out",type=Path)
    p.add_argument("--execute",action="store_true",help="Run GPU (default: inventory only)")
    a=p.parse_args()
    project=a.project.expanduser().resolve()
    binary=(a.binary or project/"results/trajectory_gate_ab/comet_hunter_trajectory_experiment").expanduser().resolve()
    if a.minutes<1 or a.minutes>20 or a.max_candidates<20 or a.max_candidates>1000:
        p.error("minutes 1..20 and max candidates 20..1000 required")
    cases=controls(Path(__file__).resolve().parents[1]/"docs/data/candidates.json")
    if a.event_id:
        cases=[c for c in cases if c["event_id"] in set(a.event_id)]
        if len(cases)!=len(set(a.event_id)):p.error("One or more control IDs unknown")
    roots=image_roots(project,a.frames_root)
    indexed,inspected,root_counts,hints=index_images(roots)
    out=(a.out or project/"results/known_comet_gate_ab").expanduser().resolve()
    out.mkdir(parents=True,exist_ok=True)
    print(f"Found {len(cases)} known-object time windows, searched {inspected} files",flush=True)
    print("Independent positional truth: "+(str(a.truth) if a.truth else "NONE: positional recall cannot be assessed"),flush=True)
    report={"generated_utc":dt.datetime.now(UTC).isoformat(),
            "mode":"real original C3 image replay", "binary":str(binary),
            "truth_file":str(a.truth) if a.truth else None,"cases":[]}
    for case in cases:
        for size in (512,1024):
            frames=pick_frames(indexed[size],case["first"],case["last"])
            item={"event_id":case["event_id"],"known_object":case["known_object"],
                  "resolution":size,"source_frames":len(frames),"runs":[]}
            report["cases"].append(item)
            if len(frames)<5:
                item["status"]="MISSING_ORIGINAL_FRAMES"
            elif verify_dimensions(frames[0],size)!="ok":
                item["status"]="INVALID_FRAME_DIMENSIONS"
            else: item["status"]="READY"
            print(f"{case['known_object']} {size}px: {len(frames)} frames ({item['status']})",flush=True)
            if item["status"]!="READY" or not a.execute:continue
            if not binary.is_file():p.error("Experimental binary not found: "+str(binary))
            for name,flags in VARIANTS.items():
                if busy_cuda():
                    item["runs"].append({"variant":name,"status":"GPU_BUSY"})
                    print("  GPU busy. Skipping remaining variants of this event.",flush=True)
                    break
                log=out/f"{case['event_id']}_{size}_{name}.log"
                print("  Running "+name,flush=True)
                try:
                    code,tracks=run_one(binary,project,frames,size,a.minutes,a.max_candidates,flags,log)
                    truth=load_truth(a.truth,case["event_id"])
                    result={"variant":name,"exit_code":code,"log":str(log)}
                    if code==0:
                        result.update(assess(tracks,case,size,truth))
                        result["candidate_cap_hit"]=len(tracks)>=a.max_candidates
                    item["runs"].append(result)
                    print("   "+json.dumps(result),flush=True)
                    if code!=0:break
                except subprocess.TimeoutExpired:
                    item["runs"].append({"variant":name,"status":"TIMEOUT","log":str(log)})
                    break
            (out/"report.json").write_text(json.dumps(report,indent=2)+"\n")
    (out/"report.json").write_text(json.dumps(report,indent=2)+"\n")
    print("REPORT:",out/"report.json",flush=True)
    print("Never claim known-comet recovery from time overlap alone.",flush=True)


if __name__=="__main__":main()
