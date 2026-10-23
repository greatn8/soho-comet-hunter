#!/usr/bin/env python3
"""Read-only analysis of experimental CCOR motion candidates.

Never fetch images, edit candidate state, restart workers or send alerts.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import statistics
from pathlib import Path

CAMERAS = ("ccor1", "ccor2")

def summary(payload):
    if payload.get("mode") != "EXPERIMENTAL_REVIEW_ONLY":
        raise ValueError("Expected the experimental CCOR review-only payload")
    tracks=payload.get("tracks")
    if not isinstance(tracks,list):
        raise ValueError("Missing CCOR tracks list")
    output={"generated_at":payload.get("generated_at"),"total_tracks":len(tracks),"cameras":{}}
    for camera in CAMERAS:
        rows=[row for row in tracks if row.get("camera")==camera]
        state=payload.get("instruments",{}).get(camera,{})
        frames=[int(row.get("frames") or 0) for row in rows]
        rms=[float(row.get("rms_px") or 0) for row in rows]
        duration=[]
        velocity_groups=collections.Counter()
        spatial_groups=collections.Counter()
        with_four=0
        inward=outward=0
        for row in rows:
            try:
                a=dt.datetime.fromisoformat(row["first"].replace("Z","+00:00"))
                b=dt.datetime.fromisoformat(row["last"].replace("Z","+00:00"))
                duration.append(max(0,(b-a).total_seconds()/60))
            except (KeyError,TypeError,ValueError):
                pass
            vx,vy=float(row.get("vx_px_h") or 0),float(row.get("vy_px_h") or 0)
            velocity_groups[(round(vx/2)*2,round(vy/2)*2)]+=1
            shape=row.get("processing_shape") or [512,512]
            try:
                x=float(row["x_first"])/float(shape[0])
                y=float(row["y_first"])/float(shape[1])
                spatial_groups[(min(3,max(0,int(x*4))),min(3,max(0,int(y*4))))]+=1
            except (KeyError,TypeError,ValueError,ZeroDivisionError):
                pass
            with_four+=int(int(row.get("frames") or 0)<=4)
            sunward=float(row.get("sunward_px_h") or 0)
            inward+=int(sunward>0)
            outward+=int(sunward<=0)
        output["cameras"][camera]={
            "feed_status":state.get("status"),
            "last_frame_utc":state.get("last_frame"),
            "frames_in_current_window":state.get("cached_recent_frames"),
            "new_images":state.get("new_images"),
            "unverified_track_count":len(rows),
            "detected_this_cycle":state.get("detected_this_cycle"),
            "cycle_hit_hard_40_track_limit":state.get("detected_this_cycle")==40,
            "four_frame_only":with_four,
            "median_frame_support":statistics.median(frames) if frames else None,
            "median_duration_minutes":round(statistics.median(duration),1) if duration else None,
            "median_rms_px":round(statistics.median(rms),2) if rms else None,
            "moving_toward_approximate_image_center":inward,
            "not_moving_toward_approximate_image_center":outward,
            "largest_velocity_groups":[{"vx_bin":vx,"vy_bin":vy,"count":count}
                    for (vx,vy),count in velocity_groups.most_common(5)],
            "largest_spatial_quadrants":[{"column":x,"row":y,"count":count}
                    for (x,y),count in spatial_groups.most_common(4)],
            "duplicate_ids":len(rows)-len({row.get("id") for row in rows}),
        }
    return output

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project",type=Path,required=True)
    ap.add_argument("--json",action="store_true",help="Machine-readable output")
    args=ap.parse_args()
    source=args.project.expanduser()/"results"/"ccor"/"ccor.json"
    report=summary(json.loads(source.read_text(encoding="utf-8")))
    if args.json:
        print(json.dumps(report,indent=2))
        return
    print("CCOR review-only diagnostic",report["generated_at"])
    print("Total hypotheses:",report["total_tracks"],"(NOT confirmed comets)")
    for name,data in report["cameras"].items():
        print("\n"+name.upper()+":")
        for key,value in data.items():
            print(" ",key+":",value)
    print("\nNo filtering or changes to running C3/CCOR were performed.")

if __name__=="__main__":
    main()
