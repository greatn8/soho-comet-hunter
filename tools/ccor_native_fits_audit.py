#!/usr/bin/env python3
"""Inspect independent CCOR report positions in native FITS arrays.

This is NOT a discovery/recovery test: reporter coordinate origin, FITS
processing level, WCS registration and source visibility must be established
independently. No SOHO/NOAA fetching or modification of production data.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
from pathlib import Path

import numpy as np
from benchmark_ccor_reported_comets import CASES

UTC = dt.timezone.utc
TIME_PATTERN = re.compile(r"(20\d{6}T\d{6})", re.I)
WCS_KEYS = (
    "CTYPE1","CTYPE2","CRPIX1","CRPIX2","CRVAL1","CRVAL2",
    "CDELT1","CDELT2","PC1_1","PC1_2","PC2_1","PC2_2",
    "CD1_1","CD1_2","CD2_1","CD2_2",
)

def iso(value):
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00","Z")

def parse_time(value):
    s=str(value).strip()
    if len(s)==15 and s[8]=="T" and s[:8].isdigit():
        return dt.datetime.strptime(s,"%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    # Accept common FITS ISO strings, including fractional seconds.
    if s.endswith("Z"):
        s=s[:-1]+"+00:00"
    parsed=dt.datetime.fromisoformat(s)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

def timestamp_from_headers(headers,name):
    for header in headers:
        for key in ("DATE-OBS","DATE_OBS","DATE-AVG"):
            if key in header:
                try:
                    return parse_time(header[key]),key
                except (ValueError,TypeError):
                    continue
    m=TIME_PATTERN.search(name)
    if m:
        return parse_time(m.group(1)),"filename"
    raise ValueError("No usable DATE-OBS or timestamp in FITS filename")

def fits_camera(path,headers):
    text=path.name.lower()
    for header in headers:
        text+=" "+str(header.get("INSTRUME","")).lower()
        text+=" "+str(header.get("TELESCOP","")).lower()
    for cam in ("ccor1","ccor2"):
        if cam in text or cam.replace("ccor","ccor-") in text:
            return cam
    return None

def load_fits(path):
    try:
        from astropy.io import fits
    except ImportError as exc:
        raise RuntimeError("astropy is required: install it in a virtualenv before running") from exc
    with fits.open(path,memmap=False) as hdul:
        headers=[hdul[0].header]
        for part in hdul:
            if part.header is not hdul[0].header:
                headers.append(part.header)
            data=part.data
            if data is None or getattr(data,"ndim",0)!=2:
                continue
            arr=np.asarray(data,dtype=np.float64).copy()
            if arr.size==0:
                continue
            when,time_source=timestamp_from_headers(headers,path.name)
            merged={}
            for h in headers:
                for key in WCS_KEYS:
                    if key in h: merged[key]=str(h[key])
            return {
                "path":str(path),
                "camera":fits_camera(path,headers),
                "timestamp":when,
                "time_source":time_source,
                "array":arr,
                "shape":list(arr.shape),
                "wcs":merged,
                "extension_name":part.name,
                "processing_level":next(
                    (str(h[k]) for h in headers for k in
                     ("LEVEL","DATALVL","PRODUCT","PROCLEV") if k in h),
                    "UNKNOWN"
                ),
            }
    raise ValueError("No 2D FITS image extension found")

def native_pixel(x,y,shape,origin):
    """No rotation or resampling, 0-based input index convention assumed."""
    height,width=shape
    if origin not in ("upper_left","lower_left"):
        raise ValueError("Specify upper_left or lower_left")
    row=float(y) if origin=="upper_left" else height-1-float(y)
    col=float(x)
    if not (0<=col<width and 0<=row<height):
        return None
    return int(round(row)),int(round(col))

def local_signal(data,x,y,origin,guard=5,outer=18):
    """Robust local brightness contrast only; not comet evidence."""
    if data.ndim!=2:
        raise ValueError("Expected 2D image")
    pos=native_pixel(x,y,data.shape,origin)
    if pos is None:
        return {"status":"OUTSIDE_NATIVE_ARRAY"}
    row,col=pos
    height,width=data.shape
    y0,y1=max(0,row-outer),min(height,row+outer+1)
    x0,x1=max(0,col-outer),min(width,col+outer+1)
    patch=data[y0:y1,x0:x1]
    yy,xx=np.mgrid[y0:y1,x0:x1]
    distance=np.maximum(np.abs(yy-row),np.abs(xx-col))
    core=patch[distance<=1]
    background=patch[(distance>=guard)&(distance<=outer)]
    core=core[np.isfinite(core)]
    background=background[np.isfinite(background)]
    if not len(core) or len(background)<60:
        return {"status":"MASKED_OR_INSUFFICIENT_DATA","row":row,"column":col}
    level=float(np.median(background))
    mad=float(np.median(np.abs(background-level)))*1.4826
    # Zero MAD is common in occulted/masked parts; never invent significance.
    if mad<1e-20:
        return {"status":"FLAT_OR_MASKED","row":row,"column":col,
                "local_core_median":float(np.median(core)),
                "local_background_median":level}
    return {
        "status":"SAMPLED_NOT_VALIDATED",
        "row":row,"column":col,
        "local_core_median":float(np.median(core)),
        "local_background_median":level,
        "local_background_robust_sigma":mad,
        "local_contrast_sigma":round((float(np.median(core))-level)/mad,3),
    }

def report_nearest(frames,target,max_minutes=4.0):
    eligible=[
        (abs((f["timestamp"]-target).total_seconds()),f)
        for f in frames
    ]
    if not eligible:
        return None
    delta,frame=min(eligible,key=lambda item:item[0])
    if delta>max_minutes*60:
        return None
    return frame

def report_for_case(case_name,frames):
    case=CASES[case_name]
    per_camera=[row for row in frames if row["camera"]==case["camera"]]
    summary={
        "case":case_name,
        "camera":case["camera"],
        "report":case["report"],
        "report_status":case["kind"],
        "native_detector_dimensions_assumed":[2048,1920],
        "coordinate_origin_confirmed":False,
        "image_registration_confirmed":False,
        "confirmed_comet_recovery":None,
        "observation_results":[],
    }
    for t,x,y in case["points"]:
        when=parse_time(t)
        row={"report_time_utc":t,"report_xy":[x,y]}
        frame=report_nearest(per_camera,when)
        if frame is None:
            row["status"]="MISSING_NATIVE_FITS"
        else:
            row.update({
                "status":"IMAGE_PRESENT_NOT_REGISTERED",
                "fits_file":frame["path"],
                "fits_time_utc":iso(frame["timestamp"]),
                "image_shape_yx":frame["shape"],
                "processing_level":frame["processing_level"],
                "wcs_metadata_present":bool(frame["wcs"]),
                "wcs":frame["wcs"],
                "origins":{
                    origin:local_signal(frame["array"],x,y,origin)
                    for origin in ("upper_left","lower_left")
                },
            })
        summary["observation_results"].append(row)
    summary["matched_native_frames"]=sum(
        r["status"]=="IMAGE_PRESENT_NOT_REGISTERED"
        for r in summary["observation_results"]
    )
    summary["report_observations"]=len(case["points"])
    summary["warning"]=(
        "These are brightness samples at raw image-array indices, not verified "
        "reported comet locations. FITS WCS and report pixel origin must be "
        "independently reconciled before measuring comet recall."
    )
    return summary

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-dir",type=Path,required=True,
                    help="Locally supplied, native CCOR FITS; directories searched recursively")
    ap.add_argument("--case",choices=tuple(CASES),action="append")
    ap.add_argument("--output",type=Path,required=True,
                    help="Offline report JSON, outside production candidate folders")
    ap.add_argument("--max-files",type=int,default=64)
    args=ap.parse_args()
    if not 1<=args.max_files<=128:
        ap.error("max-files must be 1..128")
    base=args.input_dir.expanduser().resolve()
    if not base.is_dir():
        ap.error("input-dir must be an existing directory")
    paths=sorted(
        p for p in base.rglob("*")
        if p.is_file() and (p.name.lower().endswith((".fits",".fits.gz",".fits.fz")))
    )
    if len(paths)>args.max_files:
        ap.error(f"Found {len(paths)} FITS files, over --max-files {args.max_files}")
    frames=[]
    issues=[]
    for path in paths:
        try:
            frame=load_fits(path)
            if frame["shape"]!=[1920,2048]:
                issues.append(f"{path.name}: image shape {frame['shape']} not 1920 x 2048")
            if frame["camera"] is None:
                issues.append(f"{path.name}: instrument CCOR1/CCOR2 unknown; skipped")
                continue
            frames.append(frame)
        except Exception as exc:
            issues.append(f"{path.name}: {type(exc).__name__}: {exc}")
    # A successful directory index is not proof of a successful FITS download.
    # Include manifest status so a missing dependency / failed HTTP response
    # cannot be mistaken for absent comet signal.
    manifest_path=base/"download_manifest.json"
    manifest_status={}
    if manifest_path.exists():
        try:
            manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
            for case in manifest.get("cases",[]):
                for item in case.get("files",[]):
                    status=str(item.get("status") or "UNKNOWN")
                    manifest_status[status]=manifest_status.get(status,0)+1
        except (OSError,ValueError,TypeError) as exc:
            issues.append(f"Could not parse download manifest: {exc}")
    report={
        "mode":"NATIVE_CCOR_FITS_COORDINATE_AUDIT_NOT_COMET_RECOVERY",
        "files_read":len(paths),
        "frames_loaded":len(frames),
        "problems":issues,
        "manifest_file_status_counts":manifest_status,
        "production_modified":False,
        "cases":[report_for_case(case,frames) for case in
                 dict.fromkeys(args.case or list(CASES))],
    }
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    for case in report["cases"]:
        print(case["case"],case["matched_native_frames"],
              "/",case["report_observations"],"native frames,",
              "comet recovered: UNKNOWN")
    print("Wrote",args.output)
    print(f"Input files found: {len(paths)}; readable 2D camera FITS frames: {len(frames)}")
    if manifest_status:
        print("Downloader statuses:",", ".join(
            f"{status}={count}" for status,count in sorted(manifest_status.items())))
    if issues:
        print(f"FITS input problems ({len(issues)}):")
        for problem in issues[:12]:
            print("  -",problem)
        if len(issues)>12:
            print(f"  ... and {len(issues)-12} more; see {args.output}")
    if not frames:
        print("ERROR: No usable native FITS arrays were loaded. "
              "This is a dependency/download/metadata problem, NOT a negative comet result.")
    print("No source files or running hunters were modified.")
    if not frames or not any(case["matched_native_frames"] for case in report["cases"]):
        print("ERROR: No report-times aligned with readable native FITS frames. "
              "Inspect the FITS problems and DATE-OBS values above.")
        raise SystemExit(2)

if __name__=="__main__":
    main()
