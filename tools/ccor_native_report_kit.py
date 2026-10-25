#!/usr/bin/env python3
"""Traceable CCOR comet-report pixel positions on unresized native FITS images.

Use the EXACT generated full-resolution PNG for measuring positions. One PNG
pixel is one original FITS data-array element; there is no JPEG crop,
rotation, flip, median rebin, or coordinate extrapolation. Exports source
names and SHA256 hashes, observed UTC times, positions (top-left 0-based)
and FITS image-pixel coordinates (1-based). Does not claim comet identity,
make discoveries, report to Sungrazer, or modify a live hunter.

Usage:
  python tools/ccor_native_report_kit.py prepare --fits-dir fits --output-dir audit
  python tools/ccor_native_report_kit.py export --fits-dir fits --output-dir audit --points audit/positions.json

For export, positions.json is an object:
 {"camera":"ccor1","observations":[
  {"source_file":"CCOR1_...fits","preview_sha256":"<manifest sha>","x":1234,"y":987}
 ]}
Coordinates refer to the unmodified native PNG from 'prepare', with x=0 at
the first (left) column and y=0 at the first (upper) row.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import tempfile

import numpy as np
from PIL import Image
from ccor_native_fits_audit import load_fits, iso

SHAPE=(1920,2048)
SOURCE_PATTERN=re.compile(r"CCOR[12]_[A-Za-z0-9_-]+\.fits(?:\.gz)?$",re.I)
MAX_FILES=40


def sha256_file(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda:stream.read(1<<20),b""):
            h.update(chunk)
    return h.hexdigest()


def validate_source(path):
    if not SOURCE_PATTERN.fullmatch(path.name):
        raise ValueError("Invalid native CCOR FITS source filename: "+path.name)
    record=load_fits(path)
    if tuple(record["array"].shape)!=SHAPE:
        raise ValueError("Unexpected FITS dimensions: "+str(record["array"].shape))
    if record["camera"] not in ("ccor1","ccor2"):
        raise ValueError("Camera is missing in native FITS")
    return record


def native_preview(data):
    """A display-only intensity stretch with EXACT 1:1 pixel geometry."""
    arr=np.asarray(data,dtype=np.float64)
    if arr.shape!=SHAPE:
        raise ValueError("Cannot render non-native CCOR array")
    valid=arr[np.isfinite(arr)]
    if valid.size<10000:
        raise ValueError("Too few finite science pixels for preview")
    low,high=np.percentile(valid,[3,99.9])
    if not math.isfinite(low) or not math.isfinite(high) or high<=low:
        raise ValueError("No meaningful image contrast")
    scaled=np.zeros(arr.shape,dtype=np.uint8)
    scaled[np.isfinite(arr)]=np.uint8(np.clip(
        (arr[np.isfinite(arr)]-low)/(high-low),0,1)**0.55*255)
    return Image.fromarray(scaled,mode="L")


def prepare(fits_dir,output_dir,max_files=MAX_FILES):
    sources=sorted(p for p in fits_dir.rglob("*") if p.is_file() and
                   p.name.lower().endswith((".fits",".fits.gz")))
    if not sources or len(sources)>max_files:
        raise ValueError(f"Require 1..{max_files} native FITS sources (found {len(sources)})")
    if output_dir==fits_dir or fits_dir in output_dir.parents or output_dir in fits_dir.parents:
        raise ValueError("Output directory must be separate from source FITS")
    output_dir.mkdir(parents=True,exist_ok=True)
    previews=output_dir/"native_previews"
    previews.mkdir(exist_ok=True)
    manifest={"format_version":1,"coordinate_origin":"TOP_LEFT_ZERO_BASED",
              "rendering":"DIRECT_FITS_ARRAY_NO_ROTATION_NO_RESAMPLING",
              "coordinate_system":"PIXEL_INDEX_NOT_CELESTIAL",
              "native_dimensions":[2048,1920],
              "source_to_png":"PNG x=FITS numpy column, PNG y=FITS numpy row",
              "sources":[]}
    seen=set()
    for path in sources:
        if path.name in seen:
            raise ValueError("Duplicate FITS basename cannot be uniquely referenced")
        seen.add(path.name)
        record=validate_source(path)
        dest=previews/(path.name+".native.png")
        if dest.exists():
            raise FileExistsError("Refusing to overwrite existing preview: "+str(dest))
        native_preview(record["array"]).save(dest,optimize=True)
        with Image.open(dest) as check:
            if check.size!=(2048,1920):
                raise ValueError("PNG lost source geometry")
        manifest["sources"].append({
            "source_file":path.name,"camera":record["camera"],
            "timestamp_utc":iso(record["timestamp"]),
            "source_sha256":sha256_file(path),
            "native_preview":dest.name,
            "preview_sha256":sha256_file(dest),
            "processing_level":record.get("processing_level"),
            "wcs":record.get("wcs") or {},
            "shape_yx":record["shape"],
            "time_source":record.get("time_source"),
        })
        print(record["camera"],iso(record["timestamp"]),"=>",dest.name,flush=True)
    manifest_path=output_dir/"preview_manifest.json"
    if manifest_path.exists():
        raise FileExistsError("Refusing to overwrite existing manifest")
    manifest_path.write_text(json.dumps(manifest,indent=2)+"\n")
    return manifest


def _ensure_int(value,key,max_value):
    if type(value) is not int or not (0<=value<max_value):
        raise ValueError(key+f" must be an integer 0..{max_value-1}")
    return value


def export(fits_dir,output_dir,points_path):
    manifest=json.loads((output_dir/"preview_manifest.json").read_text())
    if (manifest.get("coordinate_origin")!="TOP_LEFT_ZERO_BASED" or
        manifest.get("rendering")!="DIRECT_FITS_ARRAY_NO_ROTATION_NO_RESAMPLING"):
        raise ValueError("Unrecognised coordinate manifest; cannot export")
    proposal=json.loads(points_path.read_text())
    camera=proposal.get("camera")
    if camera not in ("ccor1","ccor2"):
        raise ValueError("Camera must be ccor1 or ccor2")
    points=proposal.get("observations")
    if not isinstance(points,list) or not (3<=len(points)<=40):
        raise ValueError("Expected 3..40 manually checked native image positions")
    available={s["source_file"]:s for s in manifest["sources"]}
    rows=[]
    visited=set()
    for p in points:
        name=p.get("source_file")
        if name not in available or name in visited:
            raise ValueError("Missing or duplicate source frame: "+str(name))
        visited.add(name)
        ref=available[name]
        if ref["camera"]!=camera:
            raise ValueError("Source is for a different camera")
        if p.get("preview_sha256")!=ref["preview_sha256"]:
            raise ValueError("Native preview SHA256 does not match input")
        matches=list(fits_dir.rglob(name))
        if len(matches)!=1:
            raise ValueError("Source file not uniquely available")
        if sha256_file(matches[0])!=ref["source_sha256"]:
            raise ValueError("Native FITS image has changed since preview")
        preview=output_dir/"native_previews"/ref["native_preview"]
        if sha256_file(preview)!=ref["preview_sha256"]:
            raise ValueError("Native image preview has changed since inspection")
        with Image.open(preview) as im:
            if im.size!=(2048,1920):
                raise ValueError("Native preview is not a 1:1 image")
        x=_ensure_int(p.get("x"),"x",2048)
        y=_ensure_int(p.get("y"),"y",1920)
        rows.append({"time_utc":ref["timestamp_utc"],"x":x,"y":y,
                     "fits_pixel_1based":[x+1,y+1],
                     "source_file":name,"source_sha256":ref["source_sha256"],
                     "preview_sha256":ref["preview_sha256"]})
    rows.sort(key=lambda r:r["time_utc"])
    if len({r["time_utc"] for r in rows})!=len(rows):
        raise ValueError("Timestamp collision or repeated science frames")
    # Separate coordinate validity from whether it is a convincing comet.
    fit=None
    if len(rows)>=3:
        from datetime import datetime
        ts=[datetime.fromisoformat(r["time_utc"].replace("Z","+00:00")) for r in rows]
        hh=np.array([(t-ts[0]).total_seconds()/3600 for t in ts])
        if np.any(np.diff(hh)<=0):
            raise ValueError("Source observation times must increase")
        xy=np.array([[r["x"],r["y"]] for r in rows],dtype=float)
        fx=np.polyfit(hh,xy[:,0],1)
        fy=np.polyfit(hh,xy[:,1],1)
        rms=float(np.sqrt(np.mean((xy[:,0]-np.polyval(fx,hh))**2+
                                  (xy[:,1]-np.polyval(fy,hh))**2)))
        fit={"time_span_hours":round(float(hh[-1]),3),
             "net_displacement_pixels":round(float(np.linalg.norm(xy[-1]-xy[0])),2),
             "speed_pixels_per_hour":round(float(math.hypot(fx[0],fy[0])),2),
             "fit_rms_pixels":round(rms,2)}
    result={"camera":camera.upper(),
            "status":"EXACT_NATIVE_IMAGE_PIXEL_COORDINATES",
            "confirmed_comet":False,
            "ready_to_submit":False,
            "source":"Original CCOR FITS image arrays rendered 1:1 to PNG",
            "report_coordinate_origin":"TOP_LEFT_ZERO_BASED",
            "fits_coordinate_origin":"FITS_1_BASED_ARRAY_INDICES",
            "native_image_dimensions":[2048,1920],
            "coordinate_transform":"IDENTITY_FROM_SOURCE_FITS_ARRAY",
            "notes":"Coordinates are exact to the specified native preview pixels. Independent source identity and comet motion must be reviewed before submitting. Provide source filenames and coordinate origin to Sungrazer.",
            "observations":rows,"motion_diagnostic":fit}
    if ((output_dir/"report_positions.json").exists() or
        (output_dir/"report_positions.txt").exists()):
        raise FileExistsError("Refusing to overwrite existing report export")
    (output_dir/"report_positions.json").write_text(json.dumps(result,indent=2)+"\n")
    lines=[camera.upper()+" — native FITS image coordinates",
           "Origin: top-left (0,0), X right, Y down; 2048x1920 pixels",
           "Date/time in UTC; source filenames in report_positions.json",
           "UNVERIFIED OBJECT — verify in source frames before submission",""]
    for row in rows:
        lines.append(f'{row["time_utc"]} {row["x"]} {row["y"]}  {row["source_file"]}')
    (output_dir/"report_positions.txt").write_text("\n".join(lines)+"\n")
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest="mode",required=True)
    for name in ("prepare","export"):
        p=sub.add_parser(name)
        p.add_argument("--fits-dir",required=True,type=Path)
        p.add_argument("--output-dir",required=True,type=Path)
        if name=="prepare":
            p.add_argument("--max-files",type=int,default=MAX_FILES)
        else:
            p.add_argument("--points",type=Path,required=True)
    args=ap.parse_args()
    fits_dir=args.fits_dir.expanduser().resolve()
    output_dir=args.output_dir.expanduser().resolve()
    if args.mode=="prepare":
        prepare(fits_dir,output_dir,max_files=args.max_files)
    else:
        output=export(fits_dir,output_dir,args.points)
        print("Coordinates exported:",len(output["observations"]),
              "Native-pixel geometry verified; comet identity UNVERIFIED")


if __name__=="__main__":
    main()
