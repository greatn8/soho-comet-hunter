#!/usr/bin/env python3
"""Read-only CCOR JPEG layout inspection for independent comet-report calibration.

Saves FULL JPEG views (not solely zoomed crops), per-frame geometry hypotheses,
and a temporal-variability map. Report coordinates must not be scored as
recovered until a physically justified JPEG/CCD mapping has been verified.

Never changes the live C3/CCOR caches, candidate state, process or alerts.
"""
from __future__ import annotations

import argparse
import io
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from benchmark_ccor_reported_comets import CASES
from ccor_report_position_overlays import (
    GEOMETRIES, available_point_images, project,
)

COLORS = {
    "full_upperleft": (255, 235, 25),
    "full_lowerleft": (30, 250, 90),
    "centered_upperleft": (255, 85, 235),
    "centered_lowerleft": (25, 235, 250),
}

def local_pixel_metrics(frame, cx, cy, half_width=12):
    """Content diagnostics only; brightness does not identify a comet."""
    a=np.asarray(frame.convert("RGB"),dtype=np.uint8)
    h,w=a.shape[:2]
    x=int(round(cx))
    y=int(round(cy))
    if x<0 or x>=w or y<0 or y>=h:
        return {"within_jpeg":False}
    region=a[max(0,y-half_width):min(h,y+half_width+1),
             max(0,x-half_width):min(w,x+half_width+1)]
    gray=region.mean(axis=2)
    return {
        "within_jpeg":True,
        "local_rgb_mean":round(float(region.mean()),2),
        "local_rgb_standard_deviation":round(float(region.std()),2),
        "local_fraction_black_rgb_lt_4":round(float(np.mean(np.max(region,axis=2)<4)),3),
        "local_gray_99th_percentile":round(float(np.percentile(gray,99)),2),
    }

def annotate_full(frame, reported_point, stamp):
    xnative,ynative=reported_point
    view=frame.copy().convert("RGB")
    draw=ImageDraw.Draw(view)
    w,h=view.size
    # Grid is for visual location only. It is not an astrometric projection.
    for k in range(1,8):
        x=round(w*k/8)
        y=round(h*k/8)
        draw.line((x,0,x,h),fill=(115,115,115),width=1)
        draw.line((0,y,w,y),fill=(115,115,115),width=1)
    metrics={}
    for name in GEOMETRIES:
        px,py=project(xnative,ynative,w,h,name)
        color=COLORS[name]
        r=18
        draw.ellipse((px-r,py-r,px+r,py+r),outline=color,width=3)
        draw.line((px-26,py,px+26,py),fill=color,width=2)
        draw.line((px,py-26,px,py+26),fill=color,width=2)
        metrics[name]={"jpeg_xy":[round(px,2),round(py,2)],
                       **local_pixel_metrics(frame,px,py)}
    draw.rectangle((0,0,w,42),fill=(12,20,31))
    draw.text((9,4),f"{stamp} UTC | report CCD point ({xnative}, {ynative})",
              fill=(255,255,255))
    draw.text((9,24),"Four locations are HYPOTHESES, not confirmed comet locations",
              fill=(255,206,85))
    return view,metrics

def temporal_map(frames):
    if len(frames)<2:
        return None
    rgb=[np.asarray(im.convert("RGB"),dtype=np.float32).mean(axis=2)
         for im in frames]
    if len({a.shape for a in rgb})!=1:
        return None
    deviation=np.std(np.stack(rgb),axis=0)
    # The scale is explicitly data dependent, intended for image-layout review.
    high=max(1.0,float(np.quantile(deviation,0.995)))
    scaled=np.clip(deviation/high*255,0,255).astype(np.uint8)
    image=Image.fromarray(scaled,"L").convert("RGB")
    draw=ImageDraw.Draw(image)
    draw.rectangle((0,0,image.width,52),fill=(12,20,31))
    draw.text((12,7),"TEMPORAL VARIATION (JPEG grayscale), NOT comet confidence",fill="white")
    draw.text((12,28),f"99.5 percentile scaling={high:.3f}; entire JPEG includes labels/borders",fill="white")
    return image

def make_report(case_id,case,output,fetch=None):
    kwargs={"fetch":fetch} if fetch is not None else {}
    frames,index_errors=available_point_images(case,**kwargs)
    good=[x for x in frames if x.get("status")=="OK"]
    rec={
        "case":case_id,
        "report_url":case["report"],
        "coordinate_origin_unverified":True,
        "indexed_report_frames":len(frames),
        "downloaded_report_frames":len(good),
        "errors":index_errors,
        "frames":[],
    }
    if not good:
        return rec
    annotated=[]
    for frame in good:
        full,metrics=annotate_full(
            frame["frame"],(frame["x"],frame["y"]),frame["time"]
        )
        name=f"{case_id}_{frame['time'].replace(':','').replace('-','')}_full_frame.png"
        path=output/name
        full.save(path)
        rec["frames"].append({
            "time":frame["time"],"jpeg_file":frame["name"],
            "dimensions":list(frame["frame"].size),
            "original_jpeg_visualization":str(path),
            "geometry_metrics":metrics,
        })
        annotated.append(full)
    # Full-field view for the first and last reported frame, at source
    # resolution. Context preserves where instrument signal and labels live.
    w,h=annotated[0].size
    canvas=Image.new("RGB",(w*2,h+112),(9,15,26))
    canvas.paste(annotated[0],(0,112))
    canvas.paste(annotated[-1],(w,112))
    draw=ImageDraw.Draw(canvas)
    draw.text((12,10),f"{case_id}: entire original NOAA JPEG field of view",fill="white")
    draw.text((12,32),"Every colored crosshair represents an UNCALIBRATED coordinate mapping",fill=(255,216,70))
    y=55
    for name,color in COLORS.items():
        draw.text((12,y),name,fill=color)
        y+=14
    fullpath=output/f"{case_id}_full_frame_comparison.png"
    canvas.save(fullpath)
    rec["full_frame_comparison"]=str(fullpath)
    variability=temporal_map([item["frame"] for item in good])
    if variability:
        varpath=output/f"{case_id}_temporal_variability.png"
        variability.save(varpath)
        rec["temporal_variability"]=str(varpath)
    return rec

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--case",action="append",choices=list(CASES))
    args=parser.parse_args()
    output=args.output_dir.expanduser().resolve()
    output.mkdir(parents=True,exist_ok=True)
    report={
        "purpose":"Full-frame pixel registration diagnostics, not comet recovery",
        "recovery_claim":None,
        "production_modified":False,
        "cases":[],
    }
    for case_id in dict.fromkeys(args.case or list(CASES)):
        try:
            row=make_report(case_id,CASES[case_id],output)
        except Exception as exc:
            row={"case":case_id,"error":f"{type(exc).__name__}: {exc}"}
        report["cases"].append(row)
        print(case_id+":",row.get("downloaded_report_frames",0),
              "exact report frames; comparison:",row.get("full_frame_comparison"),
              "error:",row.get("error"),flush=True)
    dest=output/"fullframe_registration_audit.json"
    dest.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print("Saved:",dest)
    print("No C3 or CCOR production state was read or modified.")
    if any(r.get("error") for r in report["cases"]):
        raise SystemExit(2)

if __name__=="__main__":
    main()
