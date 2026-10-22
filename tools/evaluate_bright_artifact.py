#!/usr/bin/env python3
"""Read-only evaluation of proposed bright-artifact triage on current C3 cache.

Does not fetch SOHO data, run CUDA, change hunter settings or write results.
Matches published representative IDs to latest_tracks.json for actual x/y.
"""
from __future__ import annotations
import argparse
import collections
import datetime as dt
import json
from pathlib import Path
import pixel_verify
from bright_artifact import artifact_proximity


def frame_time(name):
    return dt.datetime.strptime(str(name)[:13],"%Y%m%d_%H%M").replace(tzinfo=dt.timezone.utc)


def candidate_from_rows(row,track,resolution):
    return {
        "resolution":resolution,
        "vx":float(row["vx"]),"vy":float(row["vy"]),
        "first":{"time":frame_time(row["first"]),"x":float(track["first_x"]),
                 "y":float(track["first_y"])},
        "last":{"time":frame_time(row["last"]),"x":float(track["last_x"]),
                "y":float(track["last_y"])},
    }


def quadrant(track,resolution):
    x=(float(track["first_x"])+float(track["last_x"]))/2
    y=(float(track["first_y"])+float(track["last_y"]))/2
    return ("LOWER" if y>=resolution/2 else "UPPER")+"_"+("RIGHT" if x>=resolution/2 else "LEFT")


def evaluate(results_dir,cache_dir,resolution):
    results_dir=Path(results_dir)
    cache_dir=Path(cache_dir)
    candidates=json.loads((results_dir/"latest_candidates.json").read_text())
    tracks=json.loads((results_dir/"latest_tracks.json").read_text())
    by_id={r["candidate"]:r for r in tracks}
    frames=sorted(cache_dir.glob(f"*_c3_{resolution}.jpg"))
    if not frames:
        raise RuntimeError(f"No cached C3 {resolution} frames in {cache_dir}")
    pixel_verify.configure_resolution(resolution)
    cache={}
    inspected=[]
    missing_tracks=[]
    for row in candidates:
        if str(row.get("review_class","")).upper()!="STRONG_REVIEW":
            continue
        track=by_id.get(row.get("candidate"))
        if not track:
            missing_tracks.append(row.get("candidate"))
            continue
        c=candidate_from_rows(row,track,resolution)
        check=artifact_proximity(c,frames,cache,pixel_verify._decode_gray,frame_time)
        inspected.append({
            "candidate":row["candidate"],
            "quadrant":quadrant(track,resolution),
            "pixel_hits":(row.get("pixel_verification") or {}).get("hits"),
            "motion_family_size":row.get("motion_family_size",0),
            **check,
        })
    statuses=collections.Counter(r["status"] for r in inspected)
    spatial=collections.defaultdict(collections.Counter)
    for r in inspected:
        spatial[r["quadrant"]][r["status"]]+=1
    report={
        "source_resolution":resolution,
        "cached_frames":len(frames),
        "current_realtime_candidates":len(candidates),
        "strong_reviews_inspected":len(inspected),
        "missing_track_coordinates":missing_tracks,
        "status_counts":dict(statuses),
        "quadrant_counts":{k:dict(v) for k,v in sorted(spatial.items())},
        "proposed_downgrades":[x for x in inspected
                                if x["status"]=="ARTIFACT_ADJACENT"],
        "highest_bright_neighborhoods":sorted(
            inspected,key=lambda r:(r.get("affected_fraction",-1),
                                     r.get("peak_white_fraction",-1)),reverse=True)[:15],
    }
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project",required=True,type=Path)
    ap.add_argument("--resolution",type=int,choices=(512,1024),default=1024)
    ap.add_argument("--cache",type=Path)
    args=ap.parse_args()
    project=args.project.expanduser()
    cache=args.cache or Path("/dev/shm")/f"comet_realtime_{__import__('getpass').getuser()}_c3_{args.resolution}"
    report=evaluate(project/"results"/"realtime",cache,args.resolution)
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    main()
