#!/usr/bin/env python3
"""Offline, PRELIMINARY CCOR motion-path diagnostic against shifted null tracks.

Uses independently reported positions as *unregistered* raw-image hypotheses,
interpolating within (never beyond) the report time span and comparing the
fixed-position temporal residual score on each timestamped FITS frame.

A high rank is a reason to investigate, NEVER an independently confirmed comet
recovery. The four origin/flip hypotheses are not an established FITS-to-report
registration; Level 1A and 1B images may differ from reported display images.
No changes to live workers, cache, dashboard, notifications, or source FITS.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import math
from pathlib import Path

import numpy as np

from benchmark_ccor_reported_comets import CASES
from ccor_native_fits_audit import parse_time
from ccor_fits_temporal_controls import (
    GEOMETRIES, load_frames, residual_measurement, xy_for_shape,
)

UTC = dt.timezone.utc
# Predefined independent spatial offsets, same shift at EVERY frame time.
# Never search for the best-looking control, and do not use peak SNR to score.
GRID = (-100, -75, -50, -25, 25, 50, 75, 100)
CONTROL_OFFSETS = tuple((dx,dy) for dx in GRID for dy in GRID
                        if math.hypot(dx,dy)>=35)


def interpolate_report_position(points,when):
    """Linear interpolation through published coordinates, no extrapolation."""
    anchor=[(parse_time(t),float(x),float(y)) for t,x,y in points]
    if not anchor or when < anchor[0][0]-dt.timedelta(seconds=90) or (
        when>anchor[-1][0]+dt.timedelta(seconds=90)):
        return None
    when=max(anchor[0][0],min(anchor[-1][0],when))
    for i in range(1,len(anchor)):
        a,x0,y0=anchor[i-1]
        b,x1,y1=anchor[i]
        if a<=when<=b:
            elapsed=(b-a).total_seconds()
            if elapsed<=0:
                raise ValueError("Report times must be strictly increasing")
            fraction=(when-a).total_seconds()/elapsed
            return (x0+(x1-x0)*fraction,y0+(y1-y0)*fraction)
    return (anchor[0][1],anchor[0][2]) if len(anchor)==1 else (
        anchor[-1][1],anchor[-1][2])


def observations_for_case(frames,case):
    """Only frames within the reported observation window (+90s timestamp)."""
    rows=[]
    for index,frame in enumerate(frames):
        point=interpolate_report_position(case["points"],frame["time"])
        if point is None:
            continue
        rows.append({"frame_index":index,
                     "time":frame["time"].isoformat().replace("+00:00","Z"),
                     "file":frame["filename"],"reported_xy":[round(point[0],2),
                                                               round(point[1],2)]})
    return rows


def measure_path(arrays,observations,geometry,offset=(0,0),
                 frame_indices=None,reverse=False):
    """Score centered 3x3 median on each temporal difference image.

    Null paths retain the SAME velocity and timestamps, only translated.
    No local peak search enters the primary score.
    """
    samples=[]
    route=list(reversed([o["reported_xy"] for o in observations])) if reverse else (
        [o["reported_xy"] for o in observations])
    for o,report_xy in zip(observations,route):
        if frame_indices is not None and o["frame_index"] not in frame_indices:
            continue
        x,y=xy_for_shape(report_xy[0],report_xy[1],
                         arrays[o["frame_index"]].shape,geometry)
        x+=offset[0]
        y+=offset[1]
        r=residual_measurement(arrays,o["frame_index"],(x,y))
        status="OUTSIDE_IMAGE" if r is None else r.get("status","UNKNOWN")
        row={"file":o["file"],"time":o["time"],
             "array_xy":[round(x,2),round(y,2)],"status":status}
        if status=="MEASURED":
            z=float(r["center_snr"])
            if math.isfinite(z):
                row["center_snr"]=z
                row["peak_snr"]=r["peak_snr"]  # diagnostics only
                row["peak_offset"]=r["peak_offset"]
            else:
                row["status"]="NONFINITE_SCORE"
        samples.append(row)
    valid=[r for r in samples if r["status"]=="MEASURED"]
    values=[r["center_snr"] for r in valid]
    return {
        "samples":samples,
        "frame_count":len(samples),
        "measured":len(valid),
        "mean_center_snr":round(float(np.mean(values)),5) if values else None,
        "median_center_snr":round(float(np.median(values)),5) if values else None,
        "positive_frames":sum(z>0 for z in values),
        "min_center_snr":round(min(values),4) if values else None,
    }


def compare_path_against_nulls(arrays,observations,geometry,offsets=CONTROL_OFFSETS):
    target=measure_path(arrays,observations,geometry)
    valid_indices={o["frame_index"] for o,r in zip(observations,target["samples"])
                   if r["status"]=="MEASURED"}
    # The controls must be evaluated against the SAME timestamps, without
    # favouring an offset with only one unusually bright valid frame.
    if len(valid_indices)<4:
        return {"geometry":geometry,"status":"INSUFFICIENT_MEASURED_FRAMES",
                "target":target,"control_paths_valid":0}
    controls=[]
    for dx,dy in offsets:
        r=measure_path(arrays,observations,geometry,offset=(dx,dy),
                       frame_indices=valid_indices)
        if r["measured"]!=len(valid_indices):
            continue
        controls.append({
            "offset":[dx,dy],
            "mean_center_snr":r["mean_center_snr"],
            "median_center_snr":r["median_center_snr"],
            "positive_frames":r["positive_frames"],
        })
    reverse=measure_path(arrays,observations,geometry,reverse=True,
                         frame_indices=valid_indices)
    if len(controls)<20:
        return {"geometry":geometry,"status":"INSUFFICIENT_MATCHED_CONTROL_PATHS",
                "target":target,"reversed_path":reverse,
                "control_paths_valid":len(controls)}
    target_mean=target["mean_center_snr"]
    scores=[c["mean_center_snr"] for c in controls]
    outrank=sum(v>=target_mean for v in scores)
    # This is an empirical *local* rank, not a calibrated false-alarm rate.
    # Because four geometries were examined, do not quote 1/(N+1) as a
    # detection probability without accounting for model selection.
    return {
        "geometry":geometry,"status":"RANKED_UNCALIBRATED",
        "target":target,"reversed_path":reverse,
        "control_paths_valid":len(controls),
        "controls_at_or_above_target":outrank,
        "local_empirical_upper_tail_fraction":round((outrank+1)/(len(controls)+1),4),
        "control_mean_snr_median":round(float(np.median(scores)),4),
        "control_mean_snr_max":round(max(scores),4),
        "control_mean_snr_95pct":round(float(np.percentile(scores,95)),4),
        "top_control_paths":sorted(controls,key=lambda r:r["mean_center_snr"],
                                   reverse=True)[:8],
    }


def run(input_dir,output,cases):
    output.parent.mkdir(parents=True,exist_ok=True)
    report={"mode":"UNCALIBRATED_NATIVE_CCOR_MOTION_PATH_NULL_CONTROL",
        "registration_confirmed":False,"comet_recovered":None,
        "independently_detected":None,"production_modified":False,
        "network_requests":False,
        "warning":"Empirical offset-control rank is NOT a detection p-value or a comet recovery claim.",
        "cases":[]}
    for name in cases:
        case=CASES[name]
        frames=load_frames(input_dir,case["camera"])
        observations=observations_for_case(frames,case)
        arrays=[f["data"] for f in frames]
        entry={"case":name,"camera":case["camera"],
               "fits_count":len(frames),"report_points":len(case["points"]),
               "interpolated_frames":len(observations),
               "interpolation_span":[observations[0]["time"],observations[-1]["time"]]
                   if observations else None,
               "coordinate_mapping":"FOUR_HYPOTHESES_NOT_CALIBRATED",
               "geometry_results":[]}
        for geometry in GEOMETRIES:
            result=compare_path_against_nulls(arrays,observations,geometry)
            entry["geometry_results"].append(result)
            target=result["target"]
            print(f"  {geometry:<25} status={result['status']} "
                  f"measured={target['measured']}/{target['frame_count']} "
                  f"mean-centered={target['mean_center_snr']} "
                  f"positive={target['positive_frames']} "
                  f"controls={result.get('control_paths_valid',0)} "
                  f"fraction={result.get('local_empirical_upper_tail_fraction','N/A')}",
                  flush=True)
        ranked=[g for g in entry["geometry_results"]
                if g["status"]=="RANKED_UNCALIBRATED"]
        entry["lowest_local_tail_geometry"]=min(
            ranked,key=lambda g:g["local_empirical_upper_tail_fraction"])["geometry"] if ranked else None
        report["cases"].append(entry)
        print(f"{name}: {len(frames)} FITS, {len(observations)} frames in reported time span. "
              "No comet recovery claim.",flush=True)
    output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print("Saved isolated diagnostic:",output)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input-dir",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--case",action="append",choices=tuple(CASES))
    args=p.parse_args()
    report=run(args.input_dir.expanduser().resolve(),
               args.output.expanduser().resolve(),
               list(dict.fromkeys(args.case or CASES)))
    if any(r["interpolated_frames"]<4 for r in report["cases"]):
        raise SystemExit("Not enough FITS frames within reported track; no decision.")


if __name__=="__main__":
    main()
