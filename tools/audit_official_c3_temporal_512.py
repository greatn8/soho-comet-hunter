#!/usr/bin/env python3
"""Offline A/B test of temporal background subtraction at genuine C3 comet pixels.

Uses the SAME locally held, independently labelled SOHO-732, -1264, -1510
practice GIFs and EXACT same 512px JPEG staging as the previous CUDA 512 replay.

Compare (1) native-like raw-image positive local maxima, (2) positive
temporal residual local maxima after median background subtraction, and
(3) negative temporal residual local maxima. Evaluate ALL variants on
comet coordinates and spatially translated negative controls, with
candidate counts and distribution included so sensitivity is not
mistaken for a useful false-positive operating point.

Does not modify images in the CUDA cache, request SOHO data, run GPU, change
native candidate selection, or touch the production hunter. Template uses
the temporal median of the whole offline sequence, including future images,
so this is an IDEALIZED offline test, not an online-causal production method.
FFmpeg grayscale is approximate relative to NVJPEG used by CUDA.

  python3 tools/audit_official_c3_temporal_512.py \
    --project ~/comethunting/comet_hunter_native_cuda_v7_archive
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

import numpy as np

from benchmark_official_practice_c3 import identify_c3_training
from benchmark_official_c3_512 import CONTROL_NAMES, stage_512, truth_at_512
from diagnose_c3_shadow_streak import load_gray
from diagnose_c3_peak_vs_track import cuda_first_pass_peaks, spatial_balanced_indices
from audit_official_c3_512_points import in_cuda_field, near_peak

THRESHOLDS=(5.0, 9.75, 14.25, 18.0)
VARIANTS=("raw_bright", "temporal_bright", "temporal_dark")
OFFSETS=((-28,0),(28,0),(0,-28),(0,28))
DETECTION_RADIUS=3


def temporal_template(images):
    """One robust time-median image across the entire offline observation set."""
    data=np.asarray(images)
    if data.ndim!=3 or data.shape[1:]!=(512,512) or len(data)<5:
        raise ValueError("Need 5+ original 512x512 greyscale images")
    return np.median(data.astype(np.float32),axis=0)


def transformed(image,template,variant):
    if variant=="raw_bright":
        return image
    if variant not in ("temporal_bright","temporal_dark"):
        raise ValueError("Unknown variant: "+str(variant))
    diff=np.rint(image.astype(np.float32)-template).astype(np.int16)
    return diff if variant=="temporal_bright" else -diff


def inspect_frame(image, template, truth_xy, threshold, variant, cap=700):
    altered=transformed(image,template,variant)
    xs,ys,_=cuda_first_pass_peaks(altered,threshold=threshold)
    selection=spatial_balanced_indices(xs,ys,maximum=cap)
    x,y=map(float,truth_xy)
    truth_in_field=in_cuda_field(x,y)
    observed=near_peak(xs,ys,x,y,DETECTION_RADIUS)["hit"] if truth_in_field else False
    selected=near_peak(xs[selection],ys[selection],x,y,DETECTION_RADIUS)["hit"] if truth_in_field else False
    null_positions=[(x+dx,y+dy) for dx,dy in OFFSETS
                    if in_cuda_field(x+dx,y+dy)]
    controls=[near_peak(xs,ys,nx,ny,DETECTION_RADIUS)["hit"]
              for nx,ny in null_positions]
    return {
        "in_field":bool(truth_in_field),
        "raw_hit":bool(observed),
        "spatial_selected_hit":bool(selected),
        "control_hits":list(map(bool,controls)),
        "raw_peak_count":int(len(xs)),
        "selected_peak_count":int(len(selection)),
        "lower_left_peak_fraction":float(
            np.mean((xs<256)&(ys>=256))) if len(xs) else 0.,
    }


def stats(rows):
    eligible=[r for r in rows if r["in_field"]]
    control=[x for r in eligible for x in r["control_hits"]]
    def pct(n,d):
        return round(100*n/d,2) if d else None
    return {
        "labelled_frames":len(rows),
        "truth_in_cuda_field":len(eligible),
        "raw_truth_hits":sum(r["raw_hit"] for r in eligible),
        "raw_truth_pct":pct(sum(r["raw_hit"] for r in eligible),len(eligible)),
        "selected_truth_hits":sum(r["spatial_selected_hit"] for r in eligible),
        "selected_truth_pct":pct(sum(r["spatial_selected_hit"] for r in eligible),len(eligible)),
        "shifted_control_positions":len(control),
        "shifted_control_hits":sum(control),
        "control_hit_pct":pct(sum(control),len(control)),
        "mean_raw_peaks_per_image":round(float(np.mean([
            r["raw_peak_count"] for r in rows])),1) if rows else None,
        "mean_selected_peaks_per_image":round(float(np.mean([
            r["selected_peak_count"] for r in rows])),1) if rows else None,
        "lower_left_peak_fraction_pct":round(100*float(np.mean([
            r["lower_left_peak_fraction"] for r in rows])),2) if rows else None,
    }


def analyze_images(images,truth,thresholds=THRESHOLDS,variants=VARIANTS):
    if len(images)!=len(truth):
        raise ValueError("Image/truth count mismatch")
    reference=temporal_template(images)
    out={}
    for threshold in thresholds:
        rows={variant:[] for variant in variants}
        for gray,point in zip(images,truth):
            location=(point["x"],point["y"])
            for variant in variants:
                rows[variant].append(inspect_frame(
                    gray,reference,location,threshold,variant))
        out[str(threshold)]={
            variant:stats(results) for variant,results in rows.items()
        }
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project",type=Path,required=True)
    p.add_argument("--case",action="append",choices=CONTROL_NAMES)
    p.add_argument("--output",type=Path)
    args=p.parse_args()
    project=args.project.expanduser().resolve()
    root=project/"results"/"official_sungrazer_controls"
    report={
        "source":"Independent official SOHO C3 practice GIFs; converted via same staging as earlier 512 CUDA benchmark",
        "resolution":512,"thresholds":list(THRESHOLDS),
        "variants":list(VARIANTS),"comet_position_radius_px":DETECTION_RADIUS,
        "temporal_background":"whole-sequence median; OFFLINE idealized, not causal",
        "interpretation":"Image-level sensitivity and local shifted-control coincidence only; does not prove real comet track recovery or suppress dark moving artifacts",
        "cases":{},
    }
    for name in dict.fromkeys(args.case or CONTROL_NAMES):
        path=root/f"{name}.zip"
        if not path.is_file():
            print(f"{name}: MISSING local practice archive",flush=True)
            report["cases"][name]={"status":"MISSING_ARCHIVE"}
            continue
        case=identify_c3_training(path)
        selected=case["selected"]
        if len(selected)<5:
            print(f"{name}: too few labelled C3 observations",flush=True)
            report["cases"][name]={"status":"INSUFFICIENT_C3"}
            continue
        print(f"\n=== {name}: {len(selected)} real C3 comet-labelled images ===",
              flush=True)
        with tempfile.TemporaryDirectory(prefix="soho_temporal512_",dir="/dev/shm") as tmp:
            files=stage_512(path,selected,Path(tmp))
            grays=[load_gray(frame) for frame in files]
            truth=truth_at_512(selected)
            results=analyze_images(grays,truth)
        report["cases"][name]={"status":"OK","labelled_frames":len(truth),
                               "measurements":results}
        for threshold,methods in results.items():
            print(f"  Threshold {threshold}, spatial point cap 700:",flush=True)
            for variant,metric in methods.items():
                print(f"    {variant:<18} truth peaks {metric['raw_truth_hits']:>2}/"
                      f"{metric['truth_in_cuda_field']}  "
                      f"selected {metric['selected_truth_hits']:>2}/"
                      f"{metric['truth_in_cuda_field']} | nearby control "
                      f"{metric['control_hit_pct']}% | "
                      f"raw peaks/frame {metric['mean_raw_peaks_per_image']}",
                      flush=True)
    path=(args.output or
          project/"results"/"sungrazer_c3_512_truth_replay"/
          "temporal_difference_audit.json").expanduser().resolve()
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print("\nSaved:",path)
    print("No GPU, network requests, production detector or cached input changes.")


if __name__=="__main__":
    main()
