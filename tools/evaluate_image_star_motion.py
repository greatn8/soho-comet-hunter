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


def run(cache, results, resolution, dashboard=None, candidate_id=None,
        review_id=None):
    pixel_verify.configure_resolution(resolution)
    frames=sorted(cache.glob(f"*_c3_{resolution}.jpg"))
    candidates=json.loads((results/"latest_candidates.json").read_text())
    if dashboard:
        payload=json.loads(Path(dashboard).read_text())
        candidates.extend(payload.get("review_candidates",[]))
        candidates.extend(payload.get("latest_candidates",[]))
    if candidate_id:
        candidates=[r for r in candidates if r.get("candidate")==candidate_id]
    if review_id:
        candidates=[r for r in candidates if r.get("review_id")==review_id]

    report={"reference":None,"evaluated":0,"star_like":[],"no_consensus":0,
            "unavailable":0,"outside_reference_range":0,"details":[]}
    references={}
    seen=set()
    for row in candidates:
        if int(row.get("resolution") or resolution)!=resolution:
            continue
        key=(row.get("review_id"),row.get("first"),row.get("last"))
        if key in seen:
            continue
        seen.add(key)
        # A targeted historical candidate needs frames aligned to its actual
        # observation, not the newest frame in the rolling cache.
        end_frame=row.get("last") if candidate_id or review_id else None
        if end_frame not in references:
            references[end_frame]=build_reference(
                frames,{},pixel_verify._decode_gray,resolution,image_time,
                target_hours=2.5 if end_frame else 4,end_frame=end_frame)
        reference=references[end_frame]
        visible={key:value for key,value in reference.items()
                 if key not in ("sources","index")}
        report["reference"]=visible
        try:
            evidence=evidence_for_velocity(row,reference)
        except Exception as exc:
            evidence={"status":"UNAVAILABLE","reason":str(exc)}
        status=evidence.get("status")
        report["evaluated"]+=1
        report["details"].append({"candidate":row.get("candidate"),
            "review_id":row.get("review_id"),
            "review_class":row.get("review_class"),
            "first":row.get("first"),"last":row.get("last"),
            "speed":row.get("speed"),"reference":visible,
            "image_star_motion":evidence})
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
    ap.add_argument("--dashboard",type=Path,
                    help="Local published realtime.json for retained earlier reviews")
    ap.add_argument("--candidate-id",help="Filter to a candidate ID")
    ap.add_argument("--review-id",help="Filter to exact stable review ID")
    args=ap.parse_args()
    print(json.dumps(run(args.cache,args.results,args.resolution,
        dashboard=args.dashboard,candidate_id=args.candidate_id,
        review_id=args.review_id),indent=2))
