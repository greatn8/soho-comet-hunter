#!/usr/bin/env python3
"""Audit original SOHO C3 pixels at an independently reported potential comet.

Use this before claiming CUDA failed to detect the 2024-11-26 report. The
Sungrazer entry is labelled Potential Comet, and the published table does not
show the contributor's chosen origin; it may not be the same historical object
as the project's dashboard "SOHO-5143" record.

No SOHO requests and NO CUDA or realtime changes. Images are read and decoded
with ffmpeg, never overwritten. This diagnostic measures whether a reported
point is a local bright maximum above the SAME local-background threshold used
by the CUDA point detector (not a confirmed comet identification).

The 4 orientation trials are diagnostic alternatives, not evidence that a
particular orientation was used by the reporter.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import statistics
import subprocess
from pathlib import Path

from benchmark_reported_comet_c3 import EVENT_ID, REPORT_ID, REPORT_URL, RAW_POINTS
from benchmark_known_comets import verify_dimensions

SIZE = 1024
ORIENTATIONS = ("upper_left", "lower_left", "horizontal_flip", "rotate_180")


def mapped_point(x, y, orientation):
    x,y=int(x),int(y)
    if orientation == "upper_left":
        return x,y
    if orientation == "lower_left":
        return x,SIZE-1-y
    if orientation == "horizontal_flip":
        return SIZE-1-x,y
    if orientation == "rotate_180":
        return SIZE-1-x,SIZE-1-y
    raise ValueError("Unknown orientation")


def decode_gray(path):
    proc=subprocess.run([
        "ffmpeg","-hide_banner","-loglevel","error","-nostdin",
        "-i",str(path),"-frames:v","1","-f","rawvideo",
        "-pix_fmt","gray","-"
    ],capture_output=True,timeout=60)
    if proc.returncode:
        raise RuntimeError("ffmpeg decoder failed for "+str(path)+": "
                           +proc.stderr.decode(errors="replace")[-250:])
    if len(proc.stdout)!=SIZE*SIZE:
        raise ValueError(f"Unexpected decoded size: {len(proc.stdout)}")
    return proc.stdout


def pixel(frame,x,y):
    return frame[y*SIZE+x]


def local_residual(frame,x,y,background_radius=4):
    if min(x,y,SIZE-1-x,SIZE-1-y) < background_radius+1:
        return None
    c=pixel(frame,x,y)
    # Exact local-max convention of detectKernel: strictly brighter neighbour
    # disqualifies, equally bright neighbours do not.
    for dy in (-1,0,1):
        for dx in (-1,0,1):
            if (dx or dy) and pixel(frame,x+dx,y+dy)>c:
                return None
    ring=[]
    for dy in range(-background_radius,background_radius+1):
        for dx in range(-background_radius,background_radius+1):
            if abs(dx)<=1 and abs(dy)<=1:
                continue
            ring.append(pixel(frame,x+dx,y+dy))
    return float(c-sum(ring)/len(ring))


def audit_one(frame,all_frames,x,y,radius=6):
    # Reproduce CUDA's bright-local-max criterion for a compact neighbourhood
    # around an *independent* reference position. This is NOT a CUDA run.
    if min(x,y,SIZE-1-x,SIZE-1-y) <= 24+radius:
        return {"error":"reference too close to detector edge"}
    choices=[]
    for dy in range(-radius,radius+1):
        for dx in range(-radius,radius+1):
            xx,yy=x+dx,y+dy
            residual=local_residual(frame,xx,yy)
            if residual is None:
                continue
            temporal_baseline=statistics.median(pixel(z,xx,yy) for z in all_frames)
            choices.append({
                "x":xx,"y":yy,
                "distance_px":round(math.hypot(dx,dy),2),
                "local_residual":round(residual,2),
                "temporal_novelty":round(pixel(frame,xx,yy)-temporal_baseline,2),
                "brightness":pixel(frame,xx,yy),
            })
    choices.sort(key=lambda z:(-z["local_residual"],z["distance_px"]))
    best=choices[0] if choices else None
    return {
        "local_maxima_within_6px":len(choices),
        "peaks_above_threshold_5":sum(z["local_residual"]>=5 for z in choices),
        "peaks_above_threshold_18":sum(z["local_residual"]>=18 for z in choices),
        "best_local_maximum":best,
    }


def crop(frame,x,y,extent=38):
    # Return an 80x80 diagnostic patch in row-major grayscale.
    width=2*extent+1
    output=bytearray()
    for yy in range(y-extent,y+extent+1):
        if not (0<=yy<SIZE):
            output.extend(bytes(width))
            continue
        for xx in range(x-extent,x+extent+1):
            output.append(pixel(frame,xx,yy) if 0<=xx<SIZE else 0)
    return bytes(output)


def write_contact_sheet(gray_by_name,inputs,output):
    # 4 rows (orientations) x 5 columns (timestamps); pure standard library.
    width=77*len(inputs)
    height=77*len(ORIENTATIONS)
    cells=[]
    for orientation in ORIENTATIONS:
        row=[]
        for stamp,x,y in inputs:
            name="20241126_"+stamp+"_c3_1024.jpg"
            px,py=mapped_point(x,y,orientation)
            row.append(crop(gray_by_name[name],px,py))
        cells.append(row)
    flat=bytearray()
    for r in range(len(ORIENTATIONS)):
        for y in range(77):
            for c in range(len(inputs)):
                flat.extend(cells[r][c][y*77:(y+1)*77])
    pgm=output.with_suffix(".pgm")
    pgm.write_bytes(f"P5\n{width} {height}\n255\n".encode()+flat)
    proc=subprocess.run(
        ["ffmpeg","-hide_banner","-loglevel","error","-nostdin",
         "-y","-i",str(pgm),"-vf","scale=iw*3:ih*3:flags=neighbor",
         "-frames:v","1",str(output)],
        capture_output=True,timeout=60)
    if proc.returncode:
        raise RuntimeError("Could not convert diagnostic montage to PNG: "
                           +proc.stderr.decode(errors="replace")[-250:])
    pgm.unlink(missing_ok=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project",type=Path,required=True)
    p.add_argument("--frames-root",type=Path)
    p.add_argument("--report-dir",type=Path)
    p.add_argument("--radius",type=int,default=6)
    args=p.parse_args()
    if not 2<=args.radius<=12:
        p.error("--radius must be between 2 and 12")
    project=args.project.expanduser().resolve()
    frames=(args.frames_root.expanduser().resolve() if args.frames_root else
            project/"results"/"known_comet_inputs"/EVENT_ID)
    inputs=[(when[-8:-4],x,y) for when,x,y in RAW_POINTS]
    # The record timestamps are ISO8601; the slice above is not robust to
    # timezone formatting. Parse time components explicitly.
    inputs=[]
    for timestamp,x,y in RAW_POINTS:
        when=dt.datetime.fromisoformat(timestamp.replace("Z","+00:00"))
        inputs.append((when.strftime("%H%M"),x,y))
    files=sorted(frames.glob("20241126_*_c3_1024.jpg"))
    if len(files)<5:
        raise SystemExit(f"Need at least five original C3 1024 JPEGs in {frames}")
    missing=[f"20241126_{t}_c3_1024.jpg" for t,_,_ in inputs
             if not (frames/f"20241126_{t}_c3_1024.jpg").is_file()]
    if missing:
        raise SystemExit("Missing independently reported frame timestamps: "+
                         ", ".join(missing))
    if any(verify_dimensions(f,SIZE)!="ok" for f in files):
        raise SystemExit("One or more source files are not original 1024px JPEGs")
    all_gray={f.name:decode_gray(f) for f in files}
    collection=list(all_gray.values())
    report={
        "event_id":EVENT_ID,
        "sungrazer_report_id":REPORT_ID,
        "sungrazer_report_url":REPORT_URL,
        "report_status":"Potential Comet (not confirmed by this test)",
        "input_file_count":len(files),
        "pixels_are_original_unmodified_decoded_jpegs":True,
        "audit_radius_px":args.radius,
        "orientations":{},
    }
    for origin in ORIENTATIONS:
        rows=[]
        for stamp,x,y in inputs:
            name=f"20241126_{stamp}_c3_1024.jpg"
            xx,yy=mapped_point(x,y,origin)
            details=audit_one(all_gray[name],collection,xx,yy,args.radius)
            rows.append({"image":name,"reference_xy":[xx,yy],**details})
        report["orientations"][origin]=rows
        print(f"\n{origin}:",flush=True)
        for row in rows:
            best=row.get("best_local_maximum") or {}
            print(f"  {row['image'][9:13]}: ref={row['reference_xy']} "
                  f"maxima>=5={row.get('peaks_above_threshold_5',0)} "
                  f">=18={row.get('peaks_above_threshold_18',0)} "
                  f"best={best.get('local_residual')} "
                  f"at ({best.get('x')},{best.get('y')}) "
                  f"temporal_novelty={best.get('temporal_novelty')}",flush=True)
    output=(args.report_dir or (project/"results"/"reported_c3_pixel_audit"/
            dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")))
    output.mkdir(parents=True,exist_ok=True)
    (output/"report.json").write_text(json.dumps(report,indent=2)+"\n")
    write_contact_sheet(all_gray,inputs,output/"reference_patches.png")
    print("\nPixel audit JSON:",output/"report.json",flush=True)
    print("Reference crop montage:",output/"reference_patches.png",flush=True)
    print("Montage rows: upper_left, lower_left, horizontal_flip, rotate_180; "
          "columns: 05:30, 05:42, 05:54, 06:06, 06:18 UTC.",flush=True)
    print("NOTE: A bright peak near a reported coordinate is not confirmation "
          "of comet identity.",flush=True)


if __name__=="__main__":
    main()
