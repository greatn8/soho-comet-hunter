#!/usr/bin/env python3
"""Replay independently reported CCOR comet *candidates* without changing production.

These published/endorsed Sungrazer reports are independent reference examples,
NOT guaranteed confirmed comets. Reporting times are explicitly UTC, including
midnight crossings. Matching to displayed JPEG pixels is prohibited without
a verified detector-to-JPEG calibration.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import math
import tempfile
from pathlib import Path

from PIL import Image

import ccor_hunter as ch
from ccor_review_evidence import assess as evidence_assess
from ccor_field_rim import outer_rim_mask, track_rim_evidence
import numpy as np

UTC = dt.timezone.utc
CASES = {
    "ccor1-20261022-ruan": {
        "camera": "ccor1",
        "report": "https://sungrazer.nrl.navy.mil/index.php/report/reportjr20261022092046",
        "followup_report": "https://sungrazer.nrl.navy.mil/index.php/report/reportjr20261022101108",
        "followup_class": "Additional Observations; same contributor and spatially continuous path",
        "kind": "Endorsed potential Kreutz comet (not confirmed)",
        "native_shape": [2048,1920],
        "points": [
            ("2026-10-22T12:30:00Z",1530,1208),
            ("2026-10-22T12:45:00Z",1520,1203),
            ("2026-10-22T13:00:00Z",1510,1200),
            # Published in Jiangao Ruan's 2026-10-22 follow-up report.
            ("2026-10-22T13:15:00Z",1501,1196),
            ("2026-10-22T13:30:00Z",1492,1194),
        ],
    },
    "ccor2-20261022-wage": {
        "camera": "ccor2",
        "report": "https://sungrazer.nrl.navy.mil/report/reportcw20261022213630",
        "kind": "Endorsed potential Kreutz comet (not confirmed)",
        "native_shape": [2048,1920],
        "points": [
            ("2026-10-22T23:45:00Z",1820,1718),
            ("2026-10-23T00:30:00Z",1806,1707),
            ("2026-10-23T01:00:00Z",1796,1699),
            ("2026-10-23T01:15:00Z",1790,1695),
        ],
    },
}

def timestamp(value):
    return dt.datetime.fromisoformat(value.replace("Z","+00:00"))

def frame_window(case, before_minutes=60, after_minutes=75):
    pts=case["points"]
    return (timestamp(pts[0][0])-dt.timedelta(minutes=before_minutes),
            timestamp(pts[-1][0])+dt.timedelta(minutes=after_minutes))

def plan(case, fetch=ch.get):
    start,end=frame_window(case)
    listed,errors=ch.discover(case["camera"],end,
                              lookback_hours=(end-start).total_seconds()/3600,
                              fetch=fetch)
    selected=[x for x in listed if start<=timestamp(x["time"])<=end]
    wanted={entry[0] for entry in case["points"]}
    found={x["time"] for x in selected}
    return {
        "camera":case["camera"],
        "report":case["report"],
        "reference_kind":case["kind"],
        "interval_start_utc":ch.iso(start),
        "interval_end_utc":ch.iso(end),
        "listed_frames":len(selected),
        "report_times_covered":sum(x in found for x in wanted),
        "report_times_total":len(wanted),
        "missing_report_times":sorted(wanted-found),
        "images":selected,
        "index_errors":errors,
    }

def projected_native_point(x,y,native_shape,shape,jpeg_shape,calibration):
    """Map native CCD coordinates into our downsampled JPEG detector space.

    Calibration is REQUIRED to come from independently measured JPEG image
    boundaries, orientation and report pixel origin. Nothing is inferred.
    """
    keys=("left","top","width","height","origin")
    if not all(k in calibration for k in keys):
        raise ValueError("Missing independently measured JPEG ROI calibration")
    origin=calibration["origin"]
    if origin not in ("upper_left","lower_left"):
        raise ValueError("origin must be upper_left or lower_left")
    nx,ny=map(float,native_shape)
    jw,jh=map(float,jpeg_shape)
    w,h=map(float,shape)
    cx=x/nx
    cy=(y/ny) if origin=="upper_left" else 1-y/ny
    px=float(calibration["left"])+cx*float(calibration["width"])
    py=float(calibration["top"])+cy*float(calibration["height"])
    return px*w/jw,py*h/jh

def compare_with_calibration(case, tracks, calibration, median_cap=6.0, worst_cap=12.0):
    results=[]
    for row in tracks:
        if row.get("camera")!=case["camera"]:
            continue
        # Only times inside the track are valid. Never extrapolate a candidate.
        a,b=timestamp(row["first"]),timestamp(row["last"])
        errors=[]
        for time,x,y in case["points"]:
            t=timestamp(time)
            if not a<=t<=b:
                continue
            shape=row["processing_shape"]
            jpeg_shape=row["source_jpeg_shape"]
            px,py=projected_native_point(x,y,case["native_shape"],shape,jpeg_shape,calibration)
            hours=(t-a).total_seconds()/3600
            predict_x=row["x_first"]+row["vx_px_h"]*hours
            predict_y=row["y_first"]+row["vy_px_h"]*hours
            errors.append(math.hypot(px-predict_x,py-predict_y))
        if len(errors)>=3:
            median=sorted(errors)[len(errors)//2]
            results.append({
                "track_id":row["id"],"observations_covered":len(errors),
                "median_pixel_error":round(median,2),
                "worst_pixel_error":round(max(errors),2),
                "matched":median<=median_cap and max(errors)<=worst_cap,
            })
    results.sort(key=lambda x:(not x["matched"],x["median_pixel_error"]))
    return results

def replay(case, plan_result, calibration=None, max_frames=28):
    """Download to disposable storage, leaving realtime caches and state alone."""
    selected=plan_result["images"]
    if not selected:
        return {"status":"NO_SOURCE_IMAGES","tracks_generated":0}
    if len(selected)>max_frames:
        return {"status":"EXCEEDS_FRAME_CAP","images_requested":len(selected),
                "limit":max_frames,"tracks_generated":0}
    outcome={"status":"NOT_EVALUATED","images_requested":len(selected),
             "download_errors":[],"report_position_comparison":"UNCALIBRATED"}
    with tempfile.TemporaryDirectory(prefix="ccor_reference_",
                                     dir="/dev/shm" if Path("/dev/shm").is_dir() else None) as tmp:
        cache=Path(tmp)
        for item in selected:
            name=item["name"]
            try:
                raw=ch.get(item["url"],limit=1_500_000)
                with Image.open(io.BytesIO(raw)) as im:
                    im.verify()
                (cache/name).write_bytes(raw)
            except Exception as exc:
                outcome["download_errors"].append(f"{name}: {type(exc).__name__}: {exc}")
        end=timestamp(plan_result["interval_end_utc"])
        frames,times,sizes=ch.load_frames(cache,case["camera"],end,
                                         hours=(end-timestamp(plan_result["interval_start_utc"])).total_seconds()/3600+0.1)
        outcome["loaded_frames"]=len(frames)
        outcome["processed_frame_times_utc"]=[ch.iso(t) for t in times]
        if len(frames)<6:
            outcome["status"]="INSUFFICIENT_IMAGES"
            return outcome
        found,diag=ch.track_points(
            frames,times,sizes,case["camera"],complete_search=True,
            return_diagnostics=True)
        outcome["status"]="DETECTOR_REPLAY_COMPLETE"
        outcome["tracks_generated"]=len(found)
        outcome["search_diagnostics"]=diag
        outcome["hit_40_track_cap"]=len(found)==40
        available=[ch.iso(t) for t in times]
        by_time={ch.iso(t):image for t,image in zip(times,frames)}
        median=np.median(np.stack(frames),axis=0)
        rims={ch.iso(t):outer_rim_mask(image) for t,image in zip(times,frames)}
        rejection_counts={}
        decisions=[]
        for track in found:
            validation=evidence_assess(track,available,by_time,median)
            rim=track_rim_evidence(track,rims)
            motion=ch.annotated_track(track).get("motion_consistency",{})
            stage=ch.early_motion_review(track)
            failures=[]
            if validation["status"]!="PASS": failures.append("CONTINUITY_OR_PIXELS")
            if rim["status"]=="BOUNDARY_CONTACT": failures.append("FIELD_RIM")
            if motion.get("status")=="IRREGULAR_MOTION": failures.append("IRREGULAR_MOTION")
            if stage not in ("EARLY","ESTABLISHED"): failures.append("INSUFFICIENT_MOTION")
            for reason in failures:
                rejection_counts[reason]=rejection_counts.get(reason,0)+1
            decisions.append({"track_id":track["id"],"first":track["first"],
                "last":track["last"],"frames":track["frames"],
                "review_stage":stage,"motion":motion,
                "validation":validation,"field_rim":rim,
                "shortlist_eligible":not failures,"rejection_reasons":failures})
        outcome["pipeline_rejection_counts"]=rejection_counts
        outcome["pipeline_eligible_tracks"]=sum(x["shortlist_eligible"] for x in decisions)
        outcome["track_diagnostics"]=decisions
        outcome["report_time_coverage_only"] = plan_result["report_times_covered"]
        outcome["note"]="A track in this time window is NOT a confirmed recovery without independently calibrated report-to-JPEG registration."
        outcome["tracks_with_4_frames"]=sum(t["frames"]==4 for t in found)
        outcome["tracks_with_5plus_frames"]=sum(t["frames"]>=5 for t in found)
        outcome["sample_track_ids"]=[x["id"] for x in found[:12]]
        if calibration:
            comparisons=compare_with_calibration(case,found,calibration)
            outcome["report_position_comparison"]="CALIBRATED_BY_PROVIDED_EXTERNAL_ROI"
            outcome["best_matches"]=comparisons[:8]
            outcome["recovered_at_configured_tolerance"]=any(r["matched"] for r in comparisons)
        else:
            outcome["recovered_at_configured_tolerance"]=None
            outcome["warning"]="No independently established native-to-JPEG ROI mapping: cannot claim comet recovery or failure."
    return outcome

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case",choices=tuple(CASES),action="append",
                    help="Default: both historical reported candidates")
    ap.add_argument("--execute",action="store_true",
                    help="Download bounded JPEG set into a disposable directory and replay detector")
    ap.add_argument("--calibration",type=Path,
                    help="Optional manually verified camera-specific JPEG ROI mappings as JSON")
    ap.add_argument("--output",type=Path,
                    help="Optional separate JSON report path; never overwritten production results")
    args=ap.parse_args()
    calibrations={}
    if args.calibration:
        calibrations=json.loads(args.calibration.read_text(encoding="utf-8"))
        if not isinstance(calibrations,dict):
            ap.error("calibration JSON must be keyed by ccor1 and ccor2")
    report={"mode":"ISOLATED_CCOR_REFERENCE_REPLAY",
            "production_modified":False,"positive_controls_not_confirmed":True,
            "cases":[]}
    for name in dict.fromkeys(args.case or list(CASES)):
        case=CASES[name]
        row={"case":name}
        try:
            info=plan(case)
            images=info.pop("images")
            row.update(info)
            print(f"{name}: {info['listed_frames']} NOAA frames indexed; "
                  f"{info['report_times_covered']}/{info['report_times_total']} report times",flush=True)
            if args.execute:
                info["images"]=images
                row["replay"]=replay(case,info,calibration=calibrations.get(case["camera"]))
                print(f"  replay: {row['replay'].get('status')}, "
                      f"candidates={row['replay'].get('tracks_generated')}, "
                      f"calibrated_recovery={row['replay'].get('recovered_at_configured_tolerance')}",flush=True)
        except Exception as exc:
            row["error"]=f"{type(exc).__name__}: {exc}"
            print(f"{name}: {row['error']}",flush=True)
        report["cases"].append(row)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
        print("Wrote isolated benchmark report:",args.output)
    print("No realtime state, C3 CUDA binaries, C3/CCOR workers, or alerts were touched.")
    print("Without measured ROI calibration, this replay CANNOT claim comet recovery.")
    if any("error" in r for r in report["cases"]):
        raise SystemExit(2)

if __name__=="__main__":
    main()
