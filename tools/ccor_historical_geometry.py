#!/usr/bin/env python3
"""Inspect independently reported CCOR positions in the actual NOAA JPEGs.

This is a diagnostic, NOT a fitted image registration or confirmed comet
recovery. Four simple orientation hypotheses are enumerated explicitly,
without selecting the one that happens to show the brightest feature.
"""
import argparse
import io
import json
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
import ccor_hunter as hunter
from ccor_review_evidence import pixel_at
from ccor_report_position_overlays import project, GEOMETRIES
from benchmark_ccor_reported_comets import CASES,plan,timestamp

ORIENTATIONS=GEOMETRIES
THUMB=176
PAD=10


def sample_case(name,output,fetch=hunter.get):
    case=CASES[name]
    planned=plan(case,fetch=fetch)
    indexed=planned["images"]
    frames={}
    actual_shapes={}
    errors=[]
    for item in indexed:
        try:
            raw=fetch(item["url"],limit=1_500_000)
            with Image.open(io.BytesIO(raw)) as im:
                w,h=im.size
                factor=min(1.0,512.0/w)
                output_shape=(round(w*factor),round(h*factor))
                gray=np.asarray(im.convert("L").resize(
                    output_shape,Image.Resampling.BOX),dtype=np.float32)
            frames[item["time"]]=gray
            actual_shapes[item["time"]]=(w,h)
        except Exception as exc:
            errors.append(f"{item['time']}: {type(exc).__name__}: {exc}")
    result={"case":name,"camera":case["camera"],"report_url":case["report"],
            "reported_positions_native":case["points"],
            "indexed":len(indexed),"loaded":len(frames),"errors":errors,
            "coordinate_calibration":"NOT_CALIBRATED",
            "actual_jpeg_shapes":actual_shapes,
            "hypotheses":{},"contact_sheet":None}
    if len(frames)<6:
        print(name,"SOURCE_IMAGE_ERRORS",errors[:4],flush=True)
        return result
    baseline=np.median(np.stack(list(frames.values())),axis=0)
    columns=len(ORIENTATIONS)
    rows=len(case["points"])
    canvas=Image.new("RGB",(columns*THUMB,(rows+1)*(THUMB+25)),(15,21,31))
    draw=ImageDraw.Draw(canvas)
    for j,label in enumerate(ORIENTATIONS):
        draw.text((j*THUMB+8,9),label,fill=(245,219,106))
        n=0
        passed=0
        coordinates=[]
        for i,(when,reported_x,reported_y) in enumerate(case["points"]):
            source=frames.get(when)
            details={"time_utc":when,"native_xy":[reported_x,reported_y],
                     "image_available":source is not None}
            if source is not None:
                jw,jh=actual_shapes[when]
                xj,yj=project(reported_x,reported_y,jw,jh,label)
                h,w=source.shape
                x,y=xj*w/jw,yj*h/jh
                xpix,ypix=round(x),round(y)
                evidence=pixel_at(source,baseline,x,y)
                details.update({"processed_xy":[round(x,2),round(y,2)],
                                "pixel_evidence":evidence})
                n+=1
                passed+=int(evidence["pass"])
                residual=np.clip(source-baseline+85,0,255).astype(np.uint8)
                half=21
                x0,y0=xpix-half,ypix-half
                padded=np.full((43,43),85,dtype=np.uint8)
                ix0,iy0=max(x0,0),max(y0,0)
                ix1,iy1=min(x0+43,w),min(y0+43,h)
                if ix1>ix0 and iy1>iy0:
                    padded[iy0-y0:iy1-y0,ix0-x0:ix1-x0]=residual[iy0:iy1,ix0:ix1]
                thumb=Image.fromarray(padded).resize((THUMB,THUMB),Image.Resampling.NEAREST).convert("RGB")
                overlay=ImageDraw.Draw(thumb)
                center=THUMB//2
                overlay.ellipse((center-7,center-7,center+7,center+7),
                                outline=(255,210,20),width=2)
                canvas.paste(thumb,(j*THUMB,(i+1)*(THUMB+25)))
                draw.text((j*THUMB+5,(i+1)*(THUMB+25)-21),
                          when[11:16]+" "+("PASS" if evidence["pass"] else "no peak"),
                          fill=(200,250,205) if evidence["pass"] else (255,183,139))
            coordinates.append(details)
        result["hypotheses"][label]={"tested":n,"point_peak_passed":passed,
                                    "points":coordinates}
    output.mkdir(parents=True,exist_ok=True)
    output_file=output/(name+"_position_hypotheses.png")
    canvas.save(output_file)
    result["contact_sheet"]=str(output_file)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--case",choices=tuple(CASES),action="append")
    args=parser.parse_args()
    report={"mode":"HISTORICAL_CCOR_UNCALIBRATED_GEOMETRY_COMPARISON",
            "warnings":["No independent camera orientation or pixel-origin calibration.",
                        "A bright source at a hypothetical location does not prove a comet recovery."],
            "cases":[]}
    for name in args.case or CASES:
        result=sample_case(name,args.output_dir)
        report["cases"].append(result)
        print(name,":",result["loaded"],"frames; orientation evidence:",
              {k:str(v["point_peak_passed"])+"/"+str(v["tested"])
               for k,v in result["hypotheses"].items()},flush=True)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    target=args.output_dir/"geometry_diagnostics.json"
    target.write_text(json.dumps(report,indent=2)+"\n")
    print("Report:",target,flush=True)


if __name__=="__main__":
    main()
