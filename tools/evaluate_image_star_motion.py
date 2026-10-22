#!/usr/bin/env python3
"""Read-only image star-motion diagnostic on existing C3 JPEGs and saved tracks.

No SOHO requests, CUDA runs, edits to results, or alert changes.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pixel_verify
from image_star_motion import build_reference, evidence_for_velocity
from realtime_hunter import image_time


def run(cache, results, resolution):
    pixel_verify.configure_resolution(resolution)
    frames=sorted(cache.glob(f"*_c3_{resolution}.jpg"))
    candidates=json.loads((results/"latest_candidates.json").read_text())
    reference=build_reference(frames,{},pixel_verify._decode_gray,resolution,image_time)
    visible={key:value for key,value in reference.items() if key not in ("sources","index")}
    report={"reference":visible,"evaluated":0,"star_like":[],"no_consensus":0,
            "unavailable":0,"outside_reference_range":0,"details":[]}
    for row in candidates:
        if int(row.get("resolution") or resolution)!=resolution:
            continue
        try:
            evidence=evidence_for_velocity(row,reference)
        except Exception as exc:
            evidence={"status":"UNAVAILABLE","reason":str(exc)}
        status=evidence.get("status")
        report["evaluated"]+=1
        report["details"].append({"candidate":row.get("candidate"),
            "review_class":row.get("review_class"),
            "first":row.get("first"),"last":row.get("last"),
            "speed":row.get("speed"),"image_star_motion":evidence})
        if status=="STAR_LIKE":
            report["star_like"].append(row.get("candidate"))
        elif status=="NO_CONSENSUS":
            report["no_consensus"]+=1
        elif status=="OUTSIDE_REFERENCE_RANGE":
            report["outside_reference_range"]+=1
        else:
            report["unavailable"]+=1
    return report


if __name__=="__main__":
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache",type=Path,required=True)
    ap.add_argument("--results",type=Path,required=True)
    ap.add_argument("--resolution",type=int,choices=(512,1024),default=1024)
    args=ap.parse_args()
    print(json.dumps(run(args.cache,args.results,args.resolution),indent=2))
