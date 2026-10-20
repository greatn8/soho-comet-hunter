#!/usr/bin/env python3
"""Find where lower-left C3 artifacts enter the pipeline (offline, read-only).

Approximate native CUDA's FIRST-PASS local peaks from existing 512px JPEGs,
compare every thresholded peak, brightest 700 peaks, spatially balanced 700,
and final CUDA tracks from the same recorded 12/48/336h detector cycle.

The 9x9 surrounding-ring subtraction, 3x3 local maxima, threshold=18,
inner mask and edge mask mirror the native CUDA level-zero settings. FFmpeg
grayscale conversion may differ slightly from native NVJPEG, and sampled
frames are NOT equivalent to the full production pass. Interpret as evidence
about peak concentration, not a byte-identical detector replay.

Does not download, write to cache, use GPU, or change realtime state.

  python3 tools/diagnose_c3_peak_vs_track.py --project \
    ~/comethunting/comet_hunter_native_cuda_v7_archive
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path

import numpy as np

from diagnose_c3_shadow_streak import (
    SIZE, WINDOWS, latest_complete_logs, load_gray, choose_evenly,
    shadow_masks,
)
from realtime_hunter import parse_candidates, candidate_quadrant, image_time

QUADRANTS=("UPPER_LEFT","UPPER_RIGHT","LOWER_LEFT","LOWER_RIGHT")
MAX_POINTS=700
THRESHOLD=18.0


def moving_box_sum(img, diameter):
    """Vectorized integral-image sum, constant padding (only interior used)."""
    r=diameter//2
    a=np.pad(img.astype(np.int32),((r,r),(r,r)))
    # Integral image has explicit initial zero row/column.
    integ=np.pad(a,((1,0),(1,0))).cumsum(0).cumsum(1)
    # For a 512px input padded by r on each side,
    # the full (diameter)x(diameter) box has 512x512 output.
    return (integ[diameter:,diameter:]-integ[:-diameter,diameter:]
            -integ[diameter:,:-diameter]+integ[:-diameter,:-diameter])


def cuda_first_pass_peaks(gray, threshold=THRESHOLD):
    """Peak positions/scores mirroring detectKernel, sorted by strength/y/x."""
    if gray.shape!=(SIZE,SIZE):
        raise ValueError(f"Expected {SIZE}x{SIZE} grayscale image")
    a=gray.astype(np.float32)
    ring=(moving_box_sum(gray,9)-moving_box_sum(gray,3))/72.
    residual=a-ring
    padded=np.pad(gray,((1,1),(1,1)),mode="constant")
    max_neighbor=np.maximum.reduce([
        padded[1+dy:1+dy+SIZE,1+dx:1+dx+SIZE]
        for dy,dx in ((-1,-1),(-1,0),(-1,1),(0,-1),
                      (0,1),(1,-1),(1,0),(1,1))
    ])
    y,x=np.indices((SIZE,SIZE))
    field=(x>=24)&(x<SIZE-24)&(y>=24)&(y<SIZE-24)
    field &= (x-255.5)**2+(y-255.5)**2 >= (0.09*SIZE)**2
    eligible=field & (a>=max_neighbor) & (residual>=threshold)
    ys,xs=np.where(eligible)
    strength=residual[ys,xs]
    order=np.lexsort((xs,ys,-strength))
    return xs[order],ys[order],strength[order]


def spatial_balanced_indices(xs,ys,maximum=MAX_POINTS):
    """Match native spatial_peak_selector.hpp 75% 16x16 tile quota."""
    n=len(xs)
    if n<=maximum:
        return np.arange(n,dtype=np.int32)
    reserved=maximum*3//4
    quota=max(1,reserved//(16*16))
    bins=(ys*16//SIZE)*16+(xs*16//SIZE)
    selected=np.zeros(n,dtype=bool)
    counts=np.zeros(256,dtype=np.int32)
    admitted=0
    for i,b in enumerate(bins):
        if admitted>=reserved:
            break
        if counts[b]<quota:
            selected[i]=True
            counts[b]+=1
            admitted+=1
    if admitted<maximum:
        for i in range(n):
            if admitted>=maximum:
                break
            if not selected[i]:
                selected[i]=True
                admitted+=1
    return np.flatnonzero(selected)


def quadrant_counts(xs,ys):
    return {
        "UPPER_LEFT":int(np.count_nonzero((xs<256)&(ys<256))),
        "UPPER_RIGHT":int(np.count_nonzero((xs>=256)&(ys<256))),
        "LOWER_LEFT":int(np.count_nonzero((xs<256)&(ys>=256))),
        "LOWER_RIGHT":int(np.count_nonzero((xs>=256)&(ys>=256))),
    }


def summarize_points(xs,ys,artifact):
    counts=quadrant_counts(xs,ys)
    n=len(xs)
    hits=int(np.count_nonzero(artifact[ys,xs])) if n else 0
    return {
        "count":n,"quadrants":counts,
        "lower_left_pct":round(counts["LOWER_LEFT"]*100/n,2) if n else 0.,
        "shadow_or_edge_pct":round(hits*100/n,2) if n else 0.,
    }


def aggregate(sample_summaries):
    if not sample_summaries:
        return None
    counts={q:sum(x["quadrants"][q] for x in sample_summaries)
            for q in QUADRANTS}
    n=sum(row["count"] for row in sample_summaries)
    return {"count":n,"quadrants":counts,
            "lower_left_pct":round(counts["LOWER_LEFT"]*100/n,2) if n else 0.,
            "frames_sampled":len(sample_summaries)}


def tracks_quadrants(log):
    tracks=parse_candidates(log.read_text(encoding="utf-8",errors="replace"))
    counts={q:sum(candidate_quadrant(t)==q for t in tracks)
            for q in QUADRANTS}
    n=len(tracks)
    return {"count":n,"quadrants":counts,
            "lower_left_pct":round(counts["LOWER_LEFT"]*100/n,2) if n else 0.}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project",type=Path,required=True)
    p.add_argument("--frames-per-window",type=int,default=10)
    p.add_argument("--shadow-frames",type=int,default=15)
    p.add_argument("--output",type=Path)
    args=p.parse_args()
    if not 5<=args.frames_per_window<=20:
        p.error("--frames-per-window must be 5..20")
    if not 5<=args.shadow_frames<=30:
        p.error("--shadow-frames must be 5..30")

    project=args.project.expanduser().resolve()
    results=project/"results"/"realtime"
    runid,logs=latest_complete_logs(results)
    last=results/"last_detector_frame.txt"
    if not last.is_file():
        raise SystemExit("Missing last_detector_frame.txt; cannot bound source inputs")
    cutoff=image_time(last.read_text().strip())
    cache=Path("/dev/shm")/f"comet_realtime_{os.environ.get('USER','user')}_c3_512"
    if not cache.is_dir():
        raise SystemExit(f"Cache {cache} unavailable. No image fetch attempted")
    files=[x for x in sorted(cache.glob("*_c3_512.jpg"))
           if image_time(x.name)<=cutoff]
    if len(files)<20:
        raise SystemExit(f"Insufficient original cached frames ({len(files)})")
    shadow_files=choose_evenly(files,args.shadow_frames)
    print(f"Run {runid}: building persistent shadow/edge map "
          f"from {len(shadow_files)} original cached frames",flush=True)
    decode_cache={}
    def gray(path):
        key=str(path)
        if key not in decode_cache:
            decode_cache[key]=load_gray(path)
        return decode_cache[key]
    shadow,edge,_=shadow_masks(np.stack([gray(f) for f in shadow_files]))
    artifact=shadow|edge
    output={}
    for hours in WINDOWS:
        # Select images from the same lookback as the running detector.
        eligible=[f for f in files
                  if (cutoff-image_time(f.name)).total_seconds() <= hours*3600]
        sample=choose_evenly(eligible,args.frames_per_window)
        if not sample:
            continue
        stages={key:[] for key in ("raw_peaks","global_brightest",
                                    "spatial_balanced")}
        for frame in sample:
            xs,ys,_=cuda_first_pass_peaks(gray(frame))
            options={
                "raw_peaks":np.arange(len(xs)),
                "global_brightest":np.arange(min(MAX_POINTS,len(xs))),
                "spatial_balanced":spatial_balanced_indices(xs,ys)
            }
            for key,inds in options.items():
                stages[key].append(summarize_points(xs[inds],ys[inds],artifact))
        report={stage:aggregate(records) for stage,records in stages.items()}
        report["native_cuda_tracks"]=tracks_quadrants(logs[hours])
        report["frames_available"]=len(eligible)
        report["frame_first"]=sample[0].name
        report["frame_last"]=sample[-1].name
        output[str(hours)]=report
        print(f"\n=== {hours}h ({len(sample)} of {len(eligible)} frames sampled) ===")
        for name,entry in report.items():
            if isinstance(entry,dict) and "lower_left_pct" in entry:
                print(f"  {name:<21} {entry['count']:>6} points/tracks | "
                      f"lower-left {entry['lower_left_pct']:>6.2f}%")
    destination=(args.output or results/"peak_to_track_artifact_audit.json").expanduser().resolve()
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps({
        "run_id":runid,
        "first_pass_threshold":THRESHOLD,
        "first_pass_max_points":MAX_POINTS,
        "resolution":SIZE,
        "shadow_frames":[f.name for f in shadow_files],
        "caveat":"First pass grayscale CPU approximation on subset of JPEGs, not exact native NVJPEG detection; differences are diagnostic only",
        "windows":output
    },indent=2)+"\n")
    print("\nSaved:",destination)
    print("Read only. No CUDA processes or SOHO fetches were started.")


if __name__=="__main__":
    main()
