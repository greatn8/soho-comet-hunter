#!/usr/bin/env python3
"""Evaluate saved blind CCOR JPEG tracks with independently verified landmarks.

This must run AFTER ccor_blind_jpeg_replay has written its detector output.
No coordinates from the report are read by the blind detector. To claim a
calibrated match, provide an externally measured native-report to JPEG mapping
as pairs of image landmarks, with distinct calibration and held-out validation
points. Never calibrate using the comet candidate itself.

The independently reported cases are potential, not confirmed, comets.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import numpy as np

from benchmark_ccor_reported_comets import CASES, timestamp


def fit_affine_from_landmarks(landmarks):
    """Native XY to original JPEG XY affine with held-out validation."""
    training=[l for l in landmarks if l.get("role")=="train"]
    validation=[l for l in landmarks if l.get("role")=="validation"]
    if len(training)<4 or len(validation)<3:
        raise ValueError("Require >=4 training and >=3 held-out validation landmarks")
    def xy(rows,key):
        return np.array([v[key] for v in rows], dtype=float)
    native=xy(training,"native_xy")
    jpeg=xy(training,"jpeg_xy")
    if native.shape!=(len(training),2) or jpeg.shape!=(len(training),2):
        raise ValueError("Landmarks must have native_xy and jpeg_xy pairs")
    design=np.column_stack([native, np.ones(len(training))])
    fit,_,rank,_=np.linalg.lstsq(design,jpeg,rcond=None)
    if rank!=3:
        raise ValueError("Calibration landmarks do not span two dimensions")
    verified=np.column_stack([xy(validation,"native_xy"),
                              np.ones(len(validation))])@fit
    errors=np.linalg.norm(verified-xy(validation,"jpeg_xy"),axis=1)
    if not np.isfinite(errors).all():
        raise ValueError("Nonfinite held-out residual")
    return fit,{"train_points":len(training),
                "validation_points":len(validation),
                "validation_rms_pixels":round(float(np.sqrt(np.mean(errors**2))),4),
                "validation_worst_pixels":round(float(np.max(errors)),4)}


def compare_blind_tracks(blind, case, calibration, *,
                         rms_limit=3.0, worst_limit=6.0):
    """Score report positions only after blind tracks are already fixed."""
    transform,check=fit_affine_from_landmarks(calibration["landmarks"])
    score={"registration_status":"FAILED_VALIDATION",
           "validation":check,"blind_track_count":blind["track_count"],
           "calibrated_match":None,"matches":[]}
    limit=float(calibration.get("heldout_max_error_px",3))
    if (check["validation_rms_pixels"]>limit
            or check["validation_worst_pixels"]>2*limit):
        score["warning"]="Held-out mapping error too large for comet matching."
        return score
    if blind.get("status")!="POSITION_BLIND_REPLAY_COMPLETE":
        score["registration_status"]="PASSED_BUT_BLIND_REPLAY_INCOMPLETE"
        return score
    score["registration_status"]="VALIDATED_AFFINE"
    for row in blind["ranked_tracks"]:
        first=timestamp(row["first"])
        last=timestamp(row["last"])
        # Production processing is <=512 wide; map JPEG dimensions to it.
        original=np.asarray(row["source_jpeg_shape"],dtype=float)
        processed=np.asarray(row["processing_shape"],dtype=float)
        if (original<=0).any() or (processed<=0).any():
            continue
        observed=[]
        for t,x,y in case["points"]:
            when=timestamp(t)
            if not first<=when<=last:
                continue
            jpeg=np.array([x,y,1])@transform
            point=jpeg*processed/original
            hh=(when-first).total_seconds()/3600
            pred=np.array([row["x_first"]+row["vx_px_h"]*hh,
                           row["y_first"]+row["vy_px_h"]*hh])
            observed.append(float(np.linalg.norm(point-pred)))
        if len(observed)<3:continue
        med=float(np.median(observed))
        worst=max(observed)
        score["matches"].append({"id":row["id"],"blind_rank":row["blind_rank"],
              "covered_report_positions":len(observed),
              "median_position_error_processing_px":round(med,3),
              "worst_position_error_processing_px":round(worst,3),
              "within_tolerance":med<=rms_limit and worst<=worst_limit})
    score["matches"].sort(key=lambda r:r["median_position_error_processing_px"])
    score["best_candidate"]=score["matches"][0] if score["matches"] else None
    score["calibrated_match"]=next((r for r in score["matches"] if r["within_tolerance"]),None)
    return score


def evaluate(output_dir,calibration_file=None):
    result={"mode":"POSTHOC_CCOR_BLIND_REPLAY_EVALUATION",
            "registration_confirmed":False,
            "confirmed_comet_recovered":None,
            "report_type":"Endorsed potential comet, not confirmed",
            "cases":[]}
    calibrations=json.loads(calibration_file.read_text()) if calibration_file else {}
    for name,case in CASES.items():
        path=output_dir/("blind_jpeg_"+name+".json")
        entry={"case":name}
        if not path.exists():
            entry["status"]="NO_BLIND_REPLAY"
            result["cases"].append(entry)
            continue
        blind=json.loads(path.read_text())
        entry.update(blind_status=blind["status"],
                     source_jpeg_count=len(blind["images"]),
                     processed_frames=blind["frame_count"],
                     blind_tracks=blind["track_count"],
                     hit_40_track_cap=blind.get("hit_40_track_cap"),
                     source_errors=blind["errors"],
                     independent_detector_track_ranks=[{
                         "id":r["id"],"rank":r["blind_rank"],
                         "frames":r["frames"],"rms_px":r["rms_px"]}
                         for r in blind["ranked_tracks"][:10]])
        if case["camera"] in calibrations:
            try:
                entry["calibrated_comparison"]=compare_blind_tracks(
                    blind,case,calibrations[case["camera"]])
                entry["status"]="CALIBRATION_EVALUATED"
            except (KeyError,ValueError,TypeError,ArithmeticError) as exc:
                entry.update(status="INVALID_INDEPENDENT_CALIBRATION",
                             calibration_error=str(exc))
        else:
            entry["status"]="UNREGISTERED_BLIND_RESULTS"
        result["cases"].append(entry)
        print(name,"source",blind["status"],"tracks",blind["track_count"],
              "result",entry["status"],flush=True)
    destination=output_dir/"posthoc_blind_evaluation.json"
    destination.write_text(json.dumps(result,indent=2)+"\n")
    print("Saved:",destination)
    print("No detector inputs, caches or live hunters modified.")
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-dir",type=Path,required=True)
    ap.add_argument("--calibration",type=Path,
                   help="Independent per-camera landmark pairs; NEVER candidate positions")
    args=ap.parse_args()
    evaluate(args.output_dir.expanduser().resolve(),args.calibration)


if __name__=="__main__":main()
