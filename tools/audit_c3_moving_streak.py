#!/usr/bin/env python3
"""Read-only temporal test of moving C3 shadow/streak contamination.

Detect the strongest repeatable dark horizontal-intensity trough in the lower
left region and estimate its X coordinate in independently sampled frames.
Compare the measured shadow motion to the trajectories CUDA returned in the
same 12h/48h/336h processing cycle, with ±24px translated-path controls.

NO source-image downloads, GPU passes, detector changes, automatic rejection,
or deletion. A genuine comet may cross a shadow: this is an engineering
diagnostic, NOT a validated real-comet classifier.

Horizontal trough tracking is deliberately simple: it may miss curved,
diagonal, horizontal or multiple streaks, and may track unrelated image
features if the dominant shadow becomes weak. The matched-image comparisons
are stronger evidence than a single-frame shadow mask, not causation.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

import numpy as np

from diagnose_c3_shadow_streak import (
    SIZE, WINDOWS, latest_complete_logs, load_gray, choose_evenly, shadow_masks
)
from realtime_hunter import (
    parse_candidates, candidate_quadrant, image_time
)


def choose_shadow_region(mask, x_low=35, x_high=220):
    """Automatically locate a persistent trough and its strongest y-band."""
    options=[]
    for y0,y1 in ((256,320),(320,384),(384,448),(448,488)):
        strength=mask[y0:y1,x_low:x_high].mean(axis=0)
        for index in np.argsort(-strength,kind="stable")[:3]:
            options.append((float(strength[index]),-int(index),y0,y1,
                            x_low+int(index)))
    if not options:
        raise ValueError("No shadow scan region")
    ratio,_,y0,y1,x=max(options)
    return {"x_reference":x,"y_start":y0,"y_end":y1,
            "persistent_shadow_coverage":round(ratio,4)}


def shadow_center_in_frame(gray,reference_x,y0,y1,x_radius=28,
                           background_offset=16,min_deficit=8.):
    """One frame: locate a dark trough near reference, not a bright comet.

    Take a vertical median across a 32-64-pixel y slab to suppress compact
    single-pixel stars or comets, then compare each x column's intensity with
    two equally distant columns. Does not assume streaks remain stationary.
    """
    if gray.shape!=(SIZE,SIZE):
        raise ValueError("Expected 512x512 grayscale")
    profile=np.median(gray[y0:y1,:],axis=0).astype(np.float64)
    smooth=profile.copy()
    smooth[1:-1]=(profile[:-2]+2*profile[1:-1]+profile[2:])/4
    lo=max(24+background_offset,int(reference_x-x_radius))
    hi=min(SIZE-24-background_offset,int(reference_x+x_radius)+1)
    if hi<=lo:
        return {"measured":False,"reason":"SEARCH_REGION_OUTSIDE_IMAGE"}
    xs=np.arange(lo,hi)
    deficits=(smooth[xs-background_offset]+
              smooth[xs+background_offset])/2-smooth[xs]
    k=int(np.argmax(deficits))
    value=float(deficits[k])
    if value<min_deficit:
        return {"measured":False,"reason":"WEAK_DARK_TROUGH",
                "best_deficit":round(value,3)}
    return {"measured":True,"x":int(xs[k]),"deficit":round(value,3)}


def streak_drift_measurements(frame_rows):
    measured=[r for r in frame_rows if r["shadow"].get("measured")]
    if len(measured)<4:
        return {"reliable":False,"measured_frames":len(measured),
                "reason":"TOO_FEW_MEASURED_FRAMES"}
    t0=measured[0]["time"]
    hours=np.array([(v["time"]-t0).total_seconds()/3600 for v in measured])
    positions=np.array([v["shadow"]["x"] for v in measured],dtype=float)
    slope=float(np.polyfit(hours,positions,1)[0]) if np.ptp(hours)>0 else 0.
    return {
        "reliable":True,
        "measured_frames":len(measured),
        "x_min":int(np.min(positions)),
        "x_max":int(np.max(positions)),
        "x_range_px":round(float(np.ptp(positions)),2),
        "median_abs_frame_to_frame_shift_px":round(
            float(np.median(np.abs(np.diff(positions)))),2),
        "fitted_drift_px_per_hour":round(slope,3),
        "first_measured_x":int(positions[0]),
        "last_measured_x":int(positions[-1]),
        "movement_detected_over_2px":bool(np.ptp(positions)>=2.),
    }


def following_evidence(track,rows,region,x_shift=0,min_samples=4):
    """Does candidate X co-move with sampled shadow positions?

    Keep observations at the same timestamps as the observed shadow, and
    restrict to the measured shadow's y slab to avoid borrowing an unrelated
    feature elsewhere in the frame.
    """
    first,last=track["first"],track["last"]
    elapsed=(last["time"]-first["time"]).total_seconds()
    if elapsed<=0:
        return {"evaluable":False,"reason":"ZERO_DURATION"}
    offsets=[]
    shadow_xs=[]
    track_xs=[]
    for row in rows:
        t=row["time"]
        shadow=row["shadow"]
        if not shadow.get("measured") or t<first["time"] or t>last["time"]:
            continue
        frac=(t-first["time"]).total_seconds()/elapsed
        x=first["x"]+(last["x"]-first["x"])*frac+x_shift
        y=first["y"]+(last["y"]-first["y"])*frac
        if not (region["y_start"]<=y<region["y_end"]):
            continue
        # Track and comparison control must remain inside the same quadrant.
        if not (24<=x<256):
            continue
        offsets.append(float(x-shadow["x"]))
        shadow_xs.append(float(shadow["x"]))
        track_xs.append(float(x))
    if len(offsets)<min_samples:
        return {"evaluable":False,"reason":"INSUFFICIENT_OVERLAP",
                "measured_samples":len(offsets)}
    observed=np.array(offsets)
    shadow_range=float(np.ptp(shadow_xs))
    track_range=float(np.ptp(track_xs))
    std=float(np.std(observed))
    distance=float(np.mean(np.abs(observed)))
    # Spatial and temporal constraints. This is deliberately NOT an automatic
    # veto: authentic comet paths can overlap an instrumental shadow by chance.
    follows=bool(shadow_range>=2 and track_range>=2 and
                 std<=4.0 and distance<=14.0)
    return {
        "evaluable":True,
        "samples":len(observed),
        "mean_absolute_separation_px":round(distance,2),
        "offset_std_px":round(std,2),
        "track_x_range_px":round(track_range,2),
        "shadow_x_range_px":round(shadow_range,2),
        "co_moving_near_shadow":follows,
    }


def compare_tracks(tracks,frame_rows,region):
    by_quadrant={}
    for q in ("LOWER_LEFT","UPPER_RIGHT","UPPER_LEFT","LOWER_RIGHT"):
        group=[t for t in tracks if candidate_quadrant(t)==q]
        if q!="LOWER_LEFT":
            by_quadrant[q]={"total":len(group)}
            continue
        positives=0
        evaluated=0
        control_evaluated=0
        control_positive=0
        samples=[]
        for track in group:
            actual=following_evidence(track,frame_rows,region)
            if not actual.get("evaluable"):
                continue
            evaluated+=1
            positives+=bool(actual["co_moving_near_shadow"])
            samples.append(actual)
            for x_shift in (-24,24):
                control=following_evidence(track,frame_rows,region,
                                           x_shift=x_shift)
                if control.get("evaluable"):
                    control_evaluated+=1
                    control_positive+=bool(control["co_moving_near_shadow"])
        by_quadrant[q]={
            "total":len(group),
            "shadow_slab_evaluable":evaluated,
            "shadow_following_candidates":positives,
            "shadow_following_pct_of_evaluable":round(
                positives*100/evaluated,2) if evaluated else None,
            "translated_control_evaluable":control_evaluated,
            "translated_control_following":control_positive,
            "translated_control_following_pct":round(
                control_positive*100/control_evaluated,2)
                if control_evaluated else None,
            "median_separation_px":round(float(np.median([
                r["mean_absolute_separation_px"] for r in samples])),2)
                if samples else None,
        }
    return by_quadrant


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project",required=True,type=Path)
    p.add_argument("--samples-per-window",default=36,type=int)
    p.add_argument("--shadow-map-samples",default=15,type=int)
    p.add_argument("--output",type=Path)
    args=p.parse_args()
    if not 8<=args.samples_per_window<=100:
        p.error("--samples-per-window must be 8..100")
    if not 5<=args.shadow_map_samples<=30:
        p.error("--shadow-map-samples must be 5..30")
    project=args.project.expanduser().resolve()
    resultdir=project/"results"/"realtime"
    runid,logs=latest_complete_logs(resultdir)
    cutoff=dt.datetime.strptime(runid,"%Y%m%dT%H%M%SZ").replace(
        tzinfo=dt.timezone.utc)
    cache=Path("/dev/shm")/f"comet_realtime_{os.environ.get('USER','user')}_c3_512"
    if not cache.is_dir():
        raise SystemExit(f"Original image cache missing: {cache}")
    files=[f for f in sorted(cache.glob("*_c3_512.jpg"))
           if image_time(f.name)<=cutoff]
    if len(files)<8:
        raise SystemExit("Insufficient cached original frames; no downloads")
    mapframes=choose_evenly(files,args.shadow_map_samples)
    grays={}
    def get_gray(f):
        name=str(f)
        if name not in grays:
            grays[name]=load_gray(f)
        return grays[name]
    print(f"CUDA run {runid}: measuring moving dark streaks in original 512px "
          "cached SOHO frames; no CUDA or downloads.",flush=True)
    for f in mapframes:get_gray(f)
    shadow,_,_=shadow_masks(np.stack([get_gray(f) for f in mapframes]))
    region=choose_shadow_region(shadow)
    print("Persistent shadow scan region:",region,flush=True)
    windows={}
    for w,path in logs.items():
        frames=choose_evenly([
            f for f in files if
            (cutoff-image_time(f.name)).total_seconds()<=w*3600
        ],args.samples_per_window)
        rows=[]
        for f in frames:
            rows.append({
                "file":f.name,"time":image_time(f.name),
                "shadow":shadow_center_in_frame(get_gray(f),
                    region["x_reference"],region["y_start"],region["y_end"])
            })
        tracks=parse_candidates(path.read_text(encoding="utf-8",
                                             errors="replace"))
        motion=streak_drift_measurements(rows)
        comparison=compare_tracks(tracks,rows,region)
        windows[str(w)]={
            "native_tracks":len(tracks),
            "source_frames_sampled":len(frames),
            "streak_motion":motion,
            "track_comparison":comparison,
            "streak_center_per_frame":[{
                "frame":r["file"],"shadow":r["shadow"]} for r in rows]
        }
        lower=comparison["LOWER_LEFT"]
        print(f"\n=== {w}h | {len(frames)} sampled frames | "
              f"{len(tracks)} native CUDA tracks ===")
        print("  Dark feature measured:",motion.get("measured_frames"),
              "frames; x range:",motion.get("x_range_px"),
              "px; fitted drift:",motion.get("fitted_drift_px_per_hour"),
              "px/hour")
        print("  LL trajectories co-moving near feature:",
              lower["shadow_following_candidates"],"/",
              lower["shadow_slab_evaluable"],
              "(comparison controls:",
              lower["translated_control_following"],"/",
              lower["translated_control_evaluable"],")")
    result={
        "run_id":runid,
        "source_resolution":SIZE,
        "map_sampled_frames":[p.name for p in mapframes],
        "region":region,
        "windows":windows,
        "interpretation":"Image-column trough alignment and association, NOT proof of false detections. Tracks elsewhere or shadows moving diagonally may be missed.",
        "safety":"No production changes, no image downloads, no GPU compute",
    }
    dest=(args.output or resultdir/"moving_streak_temporal_audit.json").expanduser().resolve()
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(result,default=str,indent=2)+"\n",encoding="utf-8")
    print("\nSaved:",dest)
    print("No production filter changed. Do not reject real comets based on this diagnostic alone.")


if __name__=="__main__":
    main()
