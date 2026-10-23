#!/usr/bin/env python3
"""Offline temporal FITS residuals near independently reported CCOR trajectories.

This is an image-domain DIAGNOSTIC, not an independent astrometric registration,
comet detection, sensitivity/recall score, or confirmation. Source reports use
unknown display-coordinate mapping; compare four raw-array flips (NOT a WCS
calibration). Require separately established FITS-to-report registration before
claiming detection or a negative recovery result.

Inputs: locally downloaded FITS and independently reported UTC/XY positions.
Outputs: JSON scores and optionally PNG contact sheets. Never touches live
CCOR/C3 caches, GPU, email, SOHO, network, or dashboards.

Python dependencies: numpy, astropy, pillow.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import math
from pathlib import Path

import numpy as np
from benchmark_ccor_reported_comets import CASES
from ccor_native_fits_audit import parse_time, timestamp_from_headers, fits_camera

UTC=dt.timezone.utc
HALF=25
SHIFTS=((-45,0),(45,0),(0,-45),(0,45),(-45,-45),(45,45),
        (-45,45),(45,-45))
GEOMETRIES=("raw_upper_left","raw_lower_left","flipped_x_upper_left",
            "flipped_x_lower_left")

def xy_for_shape(x,y,shape,geometry):
    """Simple pixel conventions, not projective coordinate registration."""
    h,w=shape
    col=float(x)
    row=float(y)
    if geometry not in GEOMETRIES:
        raise ValueError("Unknown geometry")
    if geometry.startswith("flipped_x"):
        col=w-1-col
    if geometry.endswith("lower_left"):
        row=h-1-row
    return col,row

def crop(array,x,y,half=HALF):
    x,y=int(round(x)),int(round(y))
    if x-half<0 or y-half<0 or x+half>=array.shape[1] or y+half>=array.shape[0]:
        return None
    return array[y-half:y+half+1,x-half:x+half+1].copy()

def robust_sigma(values):
    finite=np.asarray(values)[np.isfinite(values)]
    if finite.size<40:
        return None
    med=float(np.median(finite))
    mad=1.4826*float(np.median(np.abs(finite-med)))
    return (med,mad) if math.isfinite(mad) and mad>1e-12 else None

def residual_measurement(frame_arrays,index,point,half=HALF):
    """Subtract temporal median from other FITS at identical array pixels."""
    x,y=point
    target=crop(frame_arrays[index],x,y,half)
    if target is None:
        return None
    others=[crop(frame,x,y,half) for j,frame in enumerate(frame_arrays) if j!=index]
    others=[v for v in others if v is not None]
    if len(others)<3:
        return None
    # Median reference rejects a passing moving source when it is absent from
    # most non-target frames, but stationary stars and changing pointing may
    # still create positive residuals. Offset controls reveal this limitation.
    with np.errstate(invalid="ignore"):
        baseline=np.nanmedian(np.stack(others),axis=0)
    residual=target-baseline
    yy,xx=np.ogrid[-half:half+1,-half:half+1]
    radius=np.maximum(np.abs(xx),np.abs(yy))
    annulus=residual[(radius>=12)&(radius<=half-2)]
    noise=robust_sigma(annulus)
    if noise is None:
        return {"status":"BAD_BACKGROUND","residual":residual}
    center,scale=noise
    def local_snr(dy,dx):
        block=residual[half+dy-1:half+dy+2,half+dx-1:half+dx+2]
        if not np.isfinite(block).all():
            return float("-inf")
        return (float(np.median(block))-center)/scale
    center_snr=local_snr(0,0)
    candidates=[(local_snr(dy,dx),dx,dy)
                for dy in range(-7,8) for dx in range(-7,8)]
    peak_snr,peak_dx,peak_dy=max(candidates,key=lambda v:v[0])
    return {
        "status":"MEASURED","center_snr":round(center_snr,3),
        "peak_snr":round(peak_snr,3),
        "peak_offset":[peak_dx,peak_dy],
        "local_sigma":round(scale,9),
        "residual":residual,
    }

def load_frames(input_dir,camera,max_files=24):
    from astropy.io import fits
    paths=sorted(
        p for p in input_dir.rglob("*.fits")
        if p.name.upper().startswith(camera.upper()+"_")
    )
    if len(paths)>max_files:
        raise ValueError("Too many FITS files for bounded offline audit")
    frames=[]
    for path in paths:
        with fits.open(path,memmap=True) as hdul:
            headers=[part.header for part in hdul]
            if fits_camera(path,headers)!=camera:
                continue
            for part in hdul:
                if part.data is None or getattr(part.data,"ndim",0)!=2:
                    continue
                when,source=timestamp_from_headers(headers,path.name)
                frames.append({"time":when,"filename":path.name,
                    "data":np.asarray(part.data,dtype=np.float32).copy(),
                    "time_source":source,
                    "processing_level":next((str(header.get(key))
                        for header in headers for key in
                        ("LEVEL","DATALVL","PRODUCT","PROCLEV")
                        if key in header),"UNKNOWN")})
                break
    return sorted(frames,key=lambda r:r["time"])

def exact_sample_frame(frames,wanted,tolerance_seconds=90):
    if not frames:
        return None
    i=min(range(len(frames)),key=lambda j:abs(
        (frames[j]["time"]-wanted).total_seconds()))
    return i if abs((frames[i]["time"]-wanted).total_seconds())<=tolerance_seconds else None

def score_geometry(frames,case,geometry,include_images=False):
    arrays=[f["data"] for f in frames]
    obs=[]
    residuals=[]
    for stamp,x,y in case["points"]:
        wanted=parse_time(stamp)
        i=exact_sample_frame(frames,wanted)
        if i is None:
            obs.append({"report_time":stamp,"status":"MISSING_FRAME"})
            continue
        pt=xy_for_shape(x,y,arrays[i].shape,geometry)
        measured=residual_measurement(arrays,i,pt)
        record={"report_time":stamp,"fits_file":frames[i]["filename"],
                "fits_time_utc":frames[i]["time"].isoformat(),
                "report_xy":[x,y],"array_xy":[round(pt[0],2),round(pt[1],2)]}
        if not measured:
            record["status"]="OUTSIDE_ARRAY"
            obs.append(record)
            continue
        record.update({k:v for k,v in measured.items() if k!="residual"})
        controls=[]
        for dx,dy in SHIFTS:
            control=residual_measurement(arrays,i,(pt[0]+dx,pt[1]+dy))
            if control and control.get("status")=="MEASURED":
                controls.append(control["peak_snr"])
        record["control_peak_median"]=round(float(np.median(controls)),3) if controls else None
        record["control_peak_max"]=round(max(controls),3) if controls else None
        record["controls_measured"]=len(controls)
        obs.append(record)
        if include_images:
            residuals.append((stamp,measured.get("residual"),record))
    measured=[p for p in obs if p["status"]=="MEASURED"]
    peaks=[p["peak_snr"] for p in measured]
    controls=[p["control_peak_max"] for p in measured if p["control_peak_max"] is not None]
    return {
        "geometry":geometry,"observations":obs,
        "measured_frames":len(measured),
        "median_center_snr":round(float(np.median([p["center_snr"] for p in measured])),3) if measured else None,
        "median_nearby_peak_snr":round(float(np.median(peaks)),3) if peaks else None,
        "frames_with_peak_at_least_4sigma":sum(p>=4 for p in peaks),
        "frames_peak_beats_all_offset_controls":sum(p["control_peak_max"] is not None
            and p["peak_snr"]>p["control_peak_max"] for p in measured),
        "median_control_max_snr":round(float(np.median(controls)),3) if controls else None,
        "images":residuals if include_images else [],
    }

def save_contact_sheet(case_name,geometry_results,output):
    from PIL import Image,ImageDraw
    tile=206
    left=205
    top=75
    rows=max((len(r["images"]) for r in geometry_results),default=0)
    if rows==0:
        return None
    canvas=Image.new("RGB",(left+tile*len(geometry_results),top+tile*rows),(18,23,31))
    draw=ImageDraw.Draw(canvas)
    draw.text((10,10),case_name+" | temporal FITS residuals | NOT astrometrically registered",
              fill="white")
    draw.text((10,31),"Yellow = reported raw-hypothesis center; green = local maximum",
              fill=(245,206,80))
    for col,result in enumerate(geometry_results):
        draw.text((left+col*tile,55),result["geometry"][:28],fill="white")
        for ri,(stamp,residual,record) in enumerate(result["images"]):
            oy=top+ri*tile
            if col==0:
                draw.text((6,oy+35),stamp.replace("T"," ")[:16],fill="white")
            if residual is None or record.get("status")!="MEASURED":
                continue
            # Identical sigma range across all panels: clipping cannot conceal
            # differences by independently stretching every candidate patch.
            scale=record["local_sigma"]
            if scale<=0:
                continue
            ref=float(np.nanmedian(residual))
            with np.errstate(invalid="ignore"):
                z=(residual-ref)/scale
            p=np.uint8(np.clip((z+4)/12*255,0,255))
            img=Image.fromarray(p,mode="L").convert("RGB").resize((tile-16,tile-16),Image.Resampling.NEAREST)
            d=ImageDraw.Draw(img)
            cx=cy=(HALF+0.5)*(tile-16)/(2*HALF+1)
            d.line((cx-9,cy,cx+9,cy),fill=(255,221,35),width=1)
            d.line((cx,cy-9,cx,cy+9),fill=(255,221,35),width=1)
            dx,dy=record["peak_offset"]
            px=cx+dx*(tile-16)/(2*HALF+1)
            py=cy+dy*(tile-16)/(2*HALF+1)
            d.ellipse((px-5,py-5,px+5,py+5),outline=(30,240,100),width=2)
            canvas.paste(img,(left+col*tile,oy+11))
            draw.text((left+col*tile+4,oy+1),
                      f"center {record['center_snr']:+.1f} peak {record['peak_snr']:+.1f}",
                      fill="white")
    path=output/(case_name+"_temporal_fits_control_sheet.png")
    canvas.save(path)
    return str(path)

def run(input_dir,output,cases,contact_sheets=True):
    output.mkdir(parents=True,exist_ok=True)
    report={"mode":"UNCALIBRATED_NATIVE_FITS_TEMPORAL_CONTROL",
      "independent_registration":False,"reported_comet_recovery":None,
      "production_modified":False,"network_access":False,
      "score_interpretation":"Exploratory local residual scores ONLY. Bright pixels can be stars/artifacts.",
      "geometries":list(GEOMETRIES),"cases":[]}
    for key in cases:
        case=CASES[key]
        rows=load_frames(input_dir,case["camera"])
        item={"case":key,"camera":case["camera"],"frames_loaded":len(rows),
              "frame_names":[r["filename"] for r in rows],
              "product_levels":sorted(set(r["processing_level"] for r in rows)),
              "reported_times":len(case["points"]),"geometry_tests":[]}
        for g in GEOMETRIES:
            result=score_geometry(rows,case,g,include_images=contact_sheets)
            result.pop("images",None) if not contact_sheets else None
            item["geometry_tests"].append(result)
        if contact_sheets:
            item["contact_sheet"]=save_contact_sheet(key,item["geometry_tests"],output)
            for r in item["geometry_tests"]:
                r.pop("images",None)
        report["cases"].append(item)
        print(f"\n{key}: {len(rows)} source FITS, product levels {item['product_levels']}",flush=True)
        for g in item["geometry_tests"]:
            print(f"  {g['geometry']:<25} frames={g['measured_frames']}"
                  f" median-center={g['median_center_snr']}"
                  f" median-local-peak={g['median_nearby_peak_snr']}"
                  f" peak>=4sigma={g['frames_with_peak_at_least_4sigma']}"
                  f" beating-controls={g['frames_peak_beats_all_offset_controls']}",
                  flush=True)
        print("  Contact sheet:",item.get("contact_sheet","disabled"),flush=True)
    dest=output/"temporal_control_audit.json"
    dest.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print("\nReport:",dest)
    print("No comet recovery claim; no live processing files changed.")
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input-dir",type=Path,required=True)
    p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--case",action="append",choices=tuple(CASES))
    p.add_argument("--no-images",action="store_true")
    args=p.parse_args()
    data=run(args.input_dir.expanduser().resolve(),
             args.output_dir.expanduser().resolve(),
             list(dict.fromkeys(args.case or CASES)),
             contact_sheets=not args.no_images)
    if not data["cases"] or any(row["frames_loaded"]<4 for row in data["cases"]):
        raise SystemExit(2)

if __name__=="__main__":
    main()
