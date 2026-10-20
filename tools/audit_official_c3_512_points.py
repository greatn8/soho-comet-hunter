#!/usr/bin/env python3
"""Where do independently confirmed sungrazers disappear at C3 512 pixels?

READ-ONLY offline, CPU-only, original official C3 practice ZIPs, no GPU, SOHO
downloads, hunter changes, or permanent staged images. Reuse EXACTLY the
GIF-to-512 JPEG conversion performed by benchmark_official_c3_512.py, then
replicate the native CUDA's approximate first-pass local-maxima/annular-residual
threshold test, global brightest point limit, and opt-in 16x16 spatial quota.

This distinguishes LOSS OF PER-FRAME DETECTIONS from LOSS DURING GPU LINKING.
It does NOT claim that a peak near a known comet proves source identity.
Nearby translated negative controls quantify coincidental matches.

  python3 tools/audit_official_c3_512_points.py --project \
    ~/comethunting/comet_hunter_native_cuda_v7_archive

A native CUDA cross-check is needed before treating these CPU/ffmpeg results
as exact; our first-pass CPU grayscale may differ from nvJPEG. The matching
radius is a diagnostic approximation (3px) and not a discovery threshold.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import tempfile

import numpy as np

from benchmark_official_practice_c3 import identify_c3_training
from benchmark_official_c3_512 import CONTROL_NAMES, stage_512, truth_at_512
from diagnose_c3_peak_vs_track import (
    cuda_first_pass_peaks, spatial_balanced_indices,
)
from diagnose_c3_shadow_streak import load_gray

SIZE=512
LEVELS=(0,5,11)
RADIUS=3
CTRL_OFFSETS=((-28,0),(28,0),(0,-28),(0,28))
FIELD_BORDER=24
CENTER_RADIUS=SIZE*0.09


def in_cuda_field(x,y):
    if not (FIELD_BORDER<=x<SIZE-FIELD_BORDER and
            FIELD_BORDER<=y<SIZE-FIELD_BORDER):
        return False
    return (x-255.5)**2+(y-255.5)**2 >= CENTER_RADIUS**2


def near_peak(xs,ys,x,y,radius=RADIUS):
    if len(xs)==0:
        return {"hit":False,"nearest_px":None}
    distance=np.hypot(xs.astype(float)-x,ys.astype(float)-y)
    nearest=float(distance.min())
    return {"hit":bool(nearest<=radius),"nearest_px":round(nearest,2)}


def level_settings(level):
    if not 0<=level<=40:
        raise ValueError("Invalid native CUDA pass level")
    return {
        "level":level,
        "threshold":max(5.,18.-level*.75),
        "max_points":min(6000,700+level*300),
    }


def point_diagnostic(gray,x,y,level):
    settings=level_settings(level)
    xs,ys,_=cuda_first_pass_peaks(gray,settings["threshold"])
    cap=settings["max_points"]
    global_ids=np.arange(min(len(xs),cap))
    balanced_ids=spatial_balanced_indices(xs,ys,maximum=cap)
    methods={
        "raw_thresholded_peaks":(xs,ys),
        "global_brightest":(xs[global_ids],ys[global_ids]),
        "spatial_balanced":(xs[balanced_ids],ys[balanced_ids]),
    }
    result={
        "in_cuda_detection_field":in_cuda_field(x,y),
        "settings":settings,
        "counts":{name:len(points[0]) for name,points in methods.items()},
        "actual":{name:near_peak(*points,x,y) for name,points in methods.items()},
        "shifted_controls":{},
    }
    for name,points in methods.items():
        control_hits=[]
        for dx,dy in CTRL_OFFSETS:
            if in_cuda_field(x+dx,y+dy):
                control_hits.append(near_peak(*points,x+dx,y+dy)["hit"])
        result["shifted_controls"][name]=control_hits
    return result


def tally(rows,method):
    infield=[r for r in rows if r["in_cuda_detection_field"]]
    positives=[r["actual"][method]["hit"] for r in infield]
    negatives=[hit for r in infield for hit in r["shifted_controls"][method]]
    return {
        "labelled_images":len(rows),
        "inside_detection_field":len(infield),
        "truth_positions_with_peak":sum(positives),
        "truth_position_hit_pct":round(100*sum(positives)/len(infield),2) if infield else None,
        "shifted_control_positions":len(negatives),
        "control_hit_pct":round(100*sum(negatives)/len(negatives),2) if negatives else None,
    }


def analyze_case(archive,selected,levels):
    truth=truth_at_512(selected)
    dataset={}
    with tempfile.TemporaryDirectory(prefix="official_c3_peak_512_",dir="/dev/shm") as tmp:
        frames=stage_512(archive,selected,Path(tmp))
        if len(frames)!=len(truth):
            raise RuntimeError("Staging count mismatch")
        for level in levels:
            dataset[level]=[]
        for index,(frame,p) in enumerate(zip(frames,truth),1):
            gray=load_gray(frame)
            for level in levels:
                row=point_diagnostic(gray,p["x"],p["y"],level)
                row["file"]=frame.name
                row["truth_x"]=p["x"]
                row["truth_y"]=p["y"]
                dataset[level].append(row)
            if index%10==0 or index==len(frames):
                print(f"  analysed {index}/{len(frames)} labelled C3 frames",flush=True)
    return {
        "source_frames":len(truth),
        "levels":{
            str(level):{
                "settings":level_settings(level),
                "summary":{method:tally(dataset[level],method) for method in
                    ("raw_thresholded_peaks","global_brightest","spatial_balanced")},
                "per_frame":dataset[level],
            } for level in levels
        },
    }


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project",required=True,type=Path)
    ap.add_argument("--case",action="append",choices=CONTROL_NAMES)
    ap.add_argument("--output",type=Path)
    args=ap.parse_args()
    project=args.project.expanduser().resolve()
    root=project/"results"/"official_sungrazer_controls"
    report={
        "input":"official independent C3 1024 practice GIFs, exactly the live-512-replay downsampling",
        "camera":"LASCO C3","resolution":SIZE,
        "method":"approximate CUDA local-maxima threshold + spatial peak cap; truth positions scaled 0.5",
        "window_first_pass_levels":list(LEVELS),
        "match_radius_px":RADIUS,
        "warning":"CPU/ffmpeg not byte-exact nvJPEG CUDA; peak coincidence alone not real-comet recovery. Diagnostics only.",
        "cases":{},
    }
    for name in dict.fromkeys(args.case or CONTROL_NAMES):
        zipfile=root/f"{name}.zip"
        if not zipfile.is_file():
            print(f"SKIP {name}: source archive missing locally",flush=True)
            report["cases"][name]={"status":"MISSING_ARCHIVE"}
            continue
        case=identify_c3_training(zipfile)
        selected=case["selected"]
        if len(selected)<5:
            print(f"SKIP {name}: <5 independently labelled C3 GIFs",flush=True)
            report["cases"][name]={"status":"INSUFFICIENT_C3_GIFS"}
            continue
        print(f"\n=== {name}: {len(selected)} official known-comet frames ===",flush=True)
        row=analyze_case(zipfile,selected,LEVELS)
        row["status"]="OK"
        report["cases"][name]=row
        for level,rec in row["levels"].items():
            print(f"  pass level={level}, threshold={rec['settings']['threshold']:.2f}, "
                  f"cap={rec['settings']['max_points']}",flush=True)
            for method,s in rec["summary"].items():
                print(f"    {method:<24}: {s['truth_positions_with_peak']:>2}/"
                      f"{s['inside_detection_field']} known positions have local peak "
                      f"({s['truth_position_hit_pct']}%) | "
                      f"shifted controls {s['control_hit_pct']}%",flush=True)
    dest=(args.output or project/"results"/"sungrazer_c3_512_truth_replay"/
          "point_stage_audit.json").expanduser().resolve()
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print("\nAudit:",dest,flush=True)
    print("No GPU used. If points survive but CUDA emits no matching trajectory, "
          "investigate GPU seed generation/linking and output filters.")


if __name__=="__main__":
    main()
