#!/usr/bin/env python3
"""CCOR reported-comet position overlays, with explicitly UNCALIBRATED maps.

Uses exactly the reported frame times from the independent Sungrazer reports.
Produces visual contact sheets for geometry inspection, never a claim of
comet recovery. No production caches, results, processes or alerts modified.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import ccor_hunter as hunter
from benchmark_ccor_reported_comets import CASES, timestamp

NATIVE_WIDTH, NATIVE_HEIGHT = 2048, 1920

def project(x, y, width, height, geometry):
    """Only illustrative geometric alternatives; these are NOT calibrations."""
    if geometry.startswith("full_"):
        left, top, iw, ih = 0, 0, width, height
    elif geometry.startswith("centered_"):
        iw,ih=width * min(1.0,1024/width),height * min(1.0,960/height)
        # NOAA's product names specify 1024x960 nominal image dimensions;
        # actual JPEG dimensions may include plotting overlays and borders.
        iw,ih=min(width,1024),min(height,960)
        left,top=(width-iw)/2,(height-ih)/2
    else:
        raise ValueError("Unknown geometry")
    u=float(x)/NATIVE_WIDTH
    v=float(y)/NATIVE_HEIGHT
    if geometry.endswith("_lowerleft"):
        v=1-v
    return (left + iw*u,top+ih*v)

GEOMETRIES = (
    "full_upperleft", "full_lowerleft",
    "centered_upperleft", "centered_lowerleft",
)

def available_point_images(case, fetch=hunter.get):
    """Fetch only the exact report-time NOAA JPEGs; bounded to 4 images."""
    earliest=timestamp(case["points"][0][0])
    latest=timestamp(case["points"][-1][0])
    index,errors=hunter.discover(
        case["camera"],latest+dt.timedelta(minutes=1),
        lookback_hours=max(2,(latest-earliest).total_seconds()/3600+1),
        fetch=fetch,
    )
    indexed={item["time"]:item for item in index}
    images=[]
    for t,x,y in case["points"]:
        found=indexed.get(t)
        if not found:
            images.append({"time":t,"x":x,"y":y,"status":"NOT_INDEXED"})
            continue
        try:
            raw=fetch(found["url"],limit=1_500_000)
            with Image.open(io.BytesIO(raw)) as im:
                frame=im.convert("RGB").copy()
            images.append({"time":t,"x":x,"y":y,"status":"OK",
                           "frame":frame,"name":found["name"]})
        except Exception as exc:
            images.append({"time":t,"x":x,"y":y,
                           "status":"DOWNLOAD_ERROR","error":str(exc)[:240]})
    return images,errors

def crosshair(frame,x,y,color=(255,230,20)):
    draw=ImageDraw.Draw(frame)
    size=14
    draw.line((x-size,y,x+size,y),fill=color,width=2)
    draw.line((x,y-size,x,y+size),fill=color,width=2)
    draw.ellipse((x-5,y-5,x+5,y+5),outline=color,width=2)

def extract_zoom(image,x,y, half=55, tile=224):
    """Shows true JPEG pixels around hypothetical center, never synthesized truth."""
    w,h=image.size
    left=int(round(x-half))
    top=int(round(y-half))
    right=int(round(x+half))
    bottom=int(round(y+half))
    crop=Image.new("RGB",(2*half,2*half),(25,25,25))
    region=image.crop((max(0,left),max(0,top),min(w,right),min(h,bottom)))
    crop.paste(region,(max(0,-left),max(0,-top)))
    zoom=crop.resize((tile,tile),Image.Resampling.NEAREST)
    crosshair(zoom,tile//2,tile//2)
    return zoom

def render(case_id,case,images,output):
    ok=[item for item in images if item["status"]=="OK"]
    if not ok:
        return None
    tile=224
    label_h=45
    table=Image.new("RGB",
        (160 + tile*len(ok), (tile+label_h)*len(GEOMETRIES)+65),
        (13,20,30))
    draw=ImageDraw.Draw(table)
    draw.text((12,8),f"{case_id}: REPORT COORDINATE GEOMETRY IS UNCALIBRATED",
              fill=(255,211,67))
    draw.text((12,29),"Yellow crosshairs are hypotheses, NOT validated comet locations",
              fill=(220,220,220))
    details=[]
    for j,item in enumerate(ok):
        time=item["time"]
        label=time.replace("T"," ")[:16]+" UTC"
        draw.text((160+j*tile,52),label,fill="white")
        for i,geometry in enumerate(GEOMETRIES):
            y0=65+i*(tile+label_h)
            if j==0:
                draw.text((8,y0+60),geometry.replace("_"," "),
                          fill="white")
            x,y=project(item["x"],item["y"],*item["frame"].size,geometry)
            tile_image=extract_zoom(item["frame"],x,y,tile=tile)
            table.paste(tile_image,(160+j*tile,y0+label_h))
            details.append({
                "geometry_hypothesis":geometry,
                "time_utc":time,
                "native_report_xy":[item["x"],item["y"]],
                "jpeg_hypothesis_xy":[round(x,2),round(y,2)],
            })
    filename=output/(case_id+"_uncalibrated_contact_sheet.png")
    table.save(filename)
    return {"contact_sheet":str(filename),
            "hypotheses":details,
            "source_frame_dimensions":[list(item["frame"].size) for item in ok]}

def run(cases,output,fetch=hunter.get):
    output.mkdir(parents=True,exist_ok=True)
    report={"mode":"UNCALIBRATED_VISUAL_GEOMETRY_DIAGNOSTIC",
            "confirmed_comet_recovery":None,
            "production_modified":False,
            "geometries":list(GEOMETRIES),
            "warning":"Do not select a map purely because a point looks bright. Independent detector-to-JPEG registration is required.",
            "cases":[]}
    for name in cases:
        case=CASES[name]
        row={"case":name,"report_url":case["report"]}
        images,errors=available_point_images(case,fetch=fetch)
        row["report_frames"]=[{
            k:v for k,v in item.items() if k!="frame"
        } for item in images]
        row["index_errors"]=errors
        row["rendered"]=render(name,case,images,output)
        report["cases"].append(row)
        print(name,":",sum(i["status"]=="OK" for i in images),
              "exact NOAA frames; sheet=",row["rendered"]["contact_sheet"] if row["rendered"] else "none",flush=True)
    dest=output/"report.json"
    dest.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    return report

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case",action="append",choices=list(CASES))
    ap.add_argument("--output-dir",type=Path,required=True)
    args=ap.parse_args()
    run(list(dict.fromkeys(args.case or CASES)),args.output_dir.expanduser().resolve())

if __name__=="__main__":
    main()
