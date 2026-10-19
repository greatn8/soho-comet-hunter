#!/usr/bin/env python3
"""Evaluate an independently reported SOHO C3 path against nearby control paths.

No CUDA or SOHO requests. Read existing original 2024-11-26 LASCO C3 JPEGs
using ffmpeg. Evaluate five reported coordinates together, at their exact
timestamped frames, rather than picking an unrelated bright peak in each.

Each pixel's reference brightness is the temporal median of ALL available
frames at that FIXED image coordinate. Subtracting it suppresses static stars
and persistent instrument structure. A 3x3 aperture minus a sparse eight
pixel annulus estimates the excess signal of a compact changing source.

For each of four plausible coordinate orientations, the same +/-2-pixel
constant registration offset is optimized for both the reported path and
spatially translated NULL paths. Nearby null paths give an empirical
relative ranking. Because adjacent controls are correlated and the report
is labelled Potential Comet, this is NOT an astronomical significance
test, an orbital solution, or confirmation of a discovered comet.

IMPORTANT: Original files are never altered. No live processes changed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import math
import shutil
import statistics
from pathlib import Path

from audit_reported_comet_pixels import decode_gray, mapped_point, SIZE, ORIENTATIONS
from benchmark_reported_comet_c3 import EVENT_ID, REPORT_ID, REPORT_URL, RAW_POINTS
from benchmark_known_comets import verify_dimensions

RING = ((-5,0),(5,0),(0,-5),(0,5),
        (-4,-4),(-4,4),(4,-4),(4,4))


def contrast(frames, frame_number, x, y, size, temporal_median):
    """Temporal-change aperture contrast. No interpolation or per-frame tuning."""
    if not (6 <= x < size-6 and 6 <= y < size-6):
        raise ValueError("Aperture too close to image boundary")
    frame=frames[frame_number]

    def excess(xx,yy):
        idx=yy*size+xx
        return int(frame[idx])-temporal_median(idx)

    source=sum(excess(x+dx,y+dy) for dy in (-1,0,1)
               for dx in (-1,0,1))/9.0
    background=sum(excess(x+dx,y+dy) for dx,dy in RING)/len(RING)
    return source-background


def trajectory_metric(values):
    """Favor persistent signal across five frames over single-frame spikes."""
    return round(statistics.median(values)
                 +0.25*min(values)+0.15*statistics.mean(values),3)


def evaluate_path(frames, anchor_frames, xy, size, temporal_median,
                  registration=2):
    """Single common offset searched across ALL anchors; no per-frame cherry-pick."""
    if len(xy)!=len(anchor_frames):
        raise ValueError("Trajectory/frame length mismatch")
    best=None
    for dy in range(-registration,registration+1):
        for dx in range(-registration,registration+1):
            coords=[(round(x)+dx,round(y)+dy) for x,y in xy]
            if any(min(x,y,size-1-x,size-1-y)<6 for x,y in coords):
                continue
            values=[round(contrast(frames,f,x,y,size,temporal_median),2)
                    for f,(x,y) in zip(anchor_frames,coords)]
            metric=trajectory_metric(values)
            if best is None or metric>best["coherent_score"]:
                best={
                    "registration_offset":[dx,dy],
                    "sample_points":coords,
                    "per_frame_temporal_excess":values,
                    "coherent_score":metric,
                    "positive_frame_count":sum(v>0 for v in values),
                    "strong_positive_frame_count":sum(v>4 for v in values)
                }
    return best


def null_offsets(step=12,extent=72,min_distance=24):
    return [(dx,dy) for dx in range(-extent,extent+1,step)
            for dy in range(-extent,extent+1,step)
            if math.hypot(dx,dy)>=min_distance]


def test_paths(frames, anchor_frames, reported_points, size=1024,
               registration=2, step=12, extent=72):
    if any(len(frame)!=size*size for frame in frames):
        raise ValueError("Unexpected decoded image dimensions")
    if len(anchor_frames)!=len(reported_points):
        raise ValueError("Anchor length mismatch")
    if not frames:
        raise ValueError("No frames")

    # Shared spatial temporal-median cache for all orientations and controls.
    @functools.lru_cache(maxsize=500000)
    def median_at(pixel_index):
        levels=sorted(frame[pixel_index] for frame in frames)
        return levels[len(levels)//2]

    results={}
    for orientation in ORIENTATIONS:
        template=[mapped_point(x,y,orientation) for x,y in reported_points]
        observed=evaluate_path(frames,anchor_frames,template,size,
                               median_at,registration)
        if observed is None:
            results[orientation]={"status":"OUTSIDE_IMAGE"}
            continue
        controls=[]
        for dx,dy in null_offsets(step,extent):
            shifted=[(x+dx,y+dy) for x,y in template]
            entry=evaluate_path(frames,anchor_frames,shifted,size,
                                median_at,registration)
            if entry is not None:
                controls.append(entry["coherent_score"])
        if not controls:
            results[orientation]={"status":"NO_VALID_CONTROLS"}
            continue
        rank=sum(c>=observed["coherent_score"] for c in controls)
        results[orientation]={
            "status":"COMPLETE",
            "reported_path":observed,
            "nearby_control_path_count":len(controls),
            "null_score_median":statistics.median(controls),
            "null_score_max":max(controls),
            "controls_scoring_at_least_as_high":rank,
            # Descriptive ranking only. Nulls overlap, orientation is uncertain
            # and 25 offsets were searched; this is NOT a calibrated p-value.
            "empirical_upper_tail_fraction":round((rank+1)/(len(controls)+1),5),
            "caution":"Exploratory nearby-null ranking, not comet confirmation"
        }
    return results


def main():
    p=argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project",type=Path,required=True)
    p.add_argument("--frames-root",type=Path)
    p.add_argument("--output",type=Path)
    p.add_argument("--null-step",type=int,default=12)
    p.add_argument("--null-extent",type=int,default=72)
    opts=p.parse_args()
    if not 8<=opts.null_step<=30 or not 24<=opts.null_extent<=120:
        p.error("null-step 8..30, null-extent 24..120")
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is required")
    project=opts.project.expanduser().resolve()
    root=opts.frames_root.expanduser().resolve() if opts.frames_root else (
        project/"results"/"known_comet_inputs"/EVENT_ID)
    images=sorted(root.glob("20241126_*_c3_1024.jpg"))
    if len(images)<8:
        raise SystemExit(f"Need original 2024-11-26 C3 frames in {root}")
    if any(verify_dimensions(p,SIZE)!="ok" for p in images):
        raise SystemExit("Not all archived frames are original 1024px JPEGs")
    idx={p.name:i for i,p in enumerate(images)}
    anchors=[]
    reference=[]
    for stamp,x,y in RAW_POINTS:
        moment=dt.datetime.fromisoformat(stamp.replace("Z","+00:00"))
        name=f"20241126_{moment:%H%M}_c3_1024.jpg"
        if name not in idx:
            raise SystemExit("Missing anchor SOHO image: "+name)
        anchors.append(idx[name])
        reference.append((x,y))
    print(f"Original frames: {len(images)} | report anchors: {len(anchors)}",flush=True)
    print(f"Published report: {REPORT_ID} (Potential Comet)",flush=True)
    print("Computing temporal-median matched-path contrasts and controls...",flush=True)
    frames=[decode_gray(p) for p in images]
    stats=test_paths(frames,anchors,reference,SIZE,
                     step=opts.null_step,extent=opts.null_extent)
    result={
        "event_id":EVENT_ID,"reported_sungrazer_entry":REPORT_ID,
        "report_url":REPORT_URL,"report_status":"Potential Comet",
        "comet_identification_confirmed":False,
        "input_images":len(images),"anchor_frames":[images[i].name for i in anchors],
        "not_a_statistical_discovery_claim":True,
        "orientations":stats,
    }
    for key,row in stats.items():
        print(f"\n{key}:",flush=True)
        if row["status"]!="COMPLETE":
            print(f"  {row['status']}",flush=True)
            continue
        trail=row["reported_path"]
        print(f"  Best constant position offset: {trail['registration_offset']}",flush=True)
        print(f"  Five per-frame temporal signals: "
              f"{trail['per_frame_temporal_excess']}",flush=True)
        print(f"  Positive frames: {trail['positive_frame_count']}/5; "
              f"strong: {trail['strong_positive_frame_count']}/5",flush=True)
        print(f"  Coherent score: {trail['coherent_score']}; "
              f"null median: {row['null_score_median']:.3f}; "
              f"null maximum: {row['null_score_max']:.3f}",flush=True)
        print(f"  Better/equal nearby shifted controls: "
              f"{row['controls_scoring_at_least_as_high']}/"
              f"{row['nearby_control_path_count']}",flush=True)
        print(f"  Empirical upper-tail fraction (descriptive only): "
              f"{row['empirical_upper_tail_fraction']}",flush=True)
    out=(opts.output.expanduser().resolve() if opts.output else
         project/"results"/"reported_c3_motion_score"/
         dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")/"report.json")
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print("\nReport:",out,flush=True)
    print("Do not treat a high rank alone as proof of a real comet.",flush=True)


if __name__=="__main__":
    main()
