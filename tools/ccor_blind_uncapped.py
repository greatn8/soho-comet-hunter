#!/usr/bin/env python3
"""Offline complete-seed CCOR test to diagnose the production 40-track cutoff.

Searches the SAME bounded 8-bit Level-3 JPEG frames with the SAME production
peak finding, speed gates, fits, motion matching, and ranking rules. Unlike the
live detector's track_points(), no return-at-40. Produces the exact original
40-prefix parity test as an integrity check.

Distinct objects are NOT identified: these are competing image-motion fits.
Hard safety budgets are explicit, reported as INCOMPLETE, never presented as
an exhaustive search when reached. No network activity and no live modifications.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

import numpy as np

import ccor_hunter as h
from ccor_blind_jpeg_replay import CASES, detector_fingerprint, validate_jpeg


def scan_without_early_return(frames,times,sizes,camera,
                              max_seed_pairs=150000,max_distinct_tracks=5000):
    """Copy of production seed logic, with early return replaced by counters.

    Important: retain candidate order and production matching logic so that
    the first 40 are byte-for-byte equivalent to h.track_points. The
    production peaks() limit=70 still exists and is disclosed.
    """
    stats={"seed_pairs_tested":0,"seed_pairs_passing_speed":0,
           "seed_pairs_passing_track_shape":0,"duplicate_tracks":0,
           "unique_tracks":0,"budget_exceeded":False,
           "search_complete":False,
           "production_peak_per_frame_limit":70}
    if len(frames)<6:
        stats["search_complete"]=True
        return [],stats
    baseline=np.median(np.stack(frames),axis=0)
    start=max(0,len(frames)-16)
    spots=[h.peaks(frame,baseline,camera) for frame in frames[start:]]
    stats["peaks_per_frame"]=[len(p) for p in spots]
    ts=times[start:]
    results=[]
    for i in range(len(spots)-3):
        if not spots[i] or not spots[i+1]:
            continue
        step=(ts[i+1]-ts[i]).total_seconds()/3600
        if not 0.12<=step<=0.65:
            continue
        for x0,y0,v0 in spots[i]:
            for x1,y1,v1 in spots[i+1]:
                stats["seed_pairs_tested"]+=1
                if stats["seed_pairs_tested"]>max_seed_pairs:
                    stats["budget_exceeded"]=True
                    stats["budget_reason"]="SEED_PAIR_LIMIT"
                    return results,stats
                speed=math.hypot(x1-x0,y1-y0)/step
                if not 0.7<=speed<=35:
                    continue
                stats["seed_pairs_passing_speed"]+=1
                vx,vy=(x1-x0)/step,(y1-y0)/step
                points=[(ts[i],x0,y0,v0),(ts[i+1],x1,y1,v1)]
                for j in range(i+2,len(spots)):
                    elapsed=(ts[j]-ts[i]).total_seconds()/3600
                    px,py=x0+vx*elapsed,y0+vy*elapsed
                    nearby=[(math.hypot(x-px,y-py),x,y,v)
                            for x,y,v in spots[j]
                            if math.hypot(x-px,y-py)<=2.5+.25*elapsed]
                    if nearby:
                        _,x,y,v=min(nearby)
                        points.append((ts[j],x,y,v))
                if len(points)<4 or (points[-1][0]-points[0][0]).total_seconds()<2700:
                    continue
                hh=np.array([(p[0]-points[0][0]).total_seconds()/3600 for p in points])
                xs=np.array([p[1] for p in points])
                ys=np.array([p[2] for p in points])
                fx=np.polyfit(hh,xs,1)
                fy=np.polyfit(hh,ys,1)
                rms=float(np.sqrt(np.mean((xs-np.polyval(fx,hh))**2+
                                           (ys-np.polyval(fy,hh))**2)))
                if rms>1.8:
                    continue
                stats["seed_pairs_passing_track_shape"]+=1
                height,width=frames[0].shape
                cx,cy=width/2,height/2
                r0=math.hypot(xs[0]-cx,ys[0]-cy)
                r1=math.hypot(xs[-1]-cx,ys[-1]-cy)
                sunward=(r0-r1)/max(.01,hh[-1])
                digest=hashlib.sha1(
                    f"{camera}:{h.iso(points[0][0])}:{round(xs[0])}:{round(ys[0])}".encode()
                ).hexdigest()[:12]
                row={
                    "id":f"{camera.upper()}-{digest}","camera":camera,
                    "review_class":"UNVERIFIED_MOTION",
                    "first":h.iso(points[0][0]),"last":h.iso(points[-1][0]),
                    "frames":len(points),"x_first":round(float(xs[0]),1),
                    "y_first":round(float(ys[0]),1),"x_last":round(float(xs[-1]),1),
                    "y_last":round(float(ys[-1]),1),
                    "vx_px_h":round(float(fx[0]),2),"vy_px_h":round(float(fy[0]),2),
                    "speed_px_h":round(float(math.hypot(fx[0],fy[0])),2),
                    "sunward_px_h":round(sunward,2),
                    "rms_px":round(rms,2),
                    "processing_shape":[width,height],
                    "source_jpeg_shape":list(sizes[start]),
                    "source":"NOAA SWPC calibrated, background-subtracted L3 JPEG",
                    "points":[{"time":h.iso(t),"x":round(x,1),"y":round(y,1)}
                              for t,x,y,_ in points],
                }
                if not any(h.match_track(old,row,None,None) for old in results):
                    results.append(row)
                    stats["unique_tracks"]=len(results)
                    if len(results)>max_distinct_tracks:
                        stats["budget_exceeded"]=True
                        stats["budget_reason"]="UNIQUE_TRACK_LIMIT"
                        return results,stats
                else:
                    stats["duplicate_tracks"]+=1
    stats["search_complete"]=True
    return results,stats


def compare_prefix(full,baseline):
    prefix=full[:min(len(full),40)]
    by_quality=sorted(prefix,key=h.track_quality,reverse=True)
    got=[{k:v for k,v in row.items() if k!="blind_rank"}
         for row in baseline["ranked_tracks"]]
    return by_quality==got


def run_one(case,source_dir,output_dir,max_seed_pairs=150000,max_tracks=5000):
    manifest_path=source_dir/("blind_jpeg_"+case+".json")
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    camera=CASES[case]["camera"]
    if not (manifest.get("blind_to_report_positions")
            and manifest.get("status")=="POSITION_BLIND_REPLAY_COMPLETE"
            and manifest.get("camera")==camera):
        raise ValueError("Missing valid position-blind baseline replay")
    sha=detector_fingerprint()
    if manifest.get("detector_source_sha256")!=sha:
        raise ValueError("Detector source fingerprint changed; baseline must be rerun")
    cache=source_dir/case/"jpegs"
    good=[row for row in manifest["images"] if row["status"]=="VALIDATED"]
    if len(good)!=manifest["frame_count"]:
        raise ValueError("Reference JPEG manifest differs from processed frames")
    for row in good:
        path=cache/row["name"]
        if validate_jpeg(path)["sha256"]!=row["sha256"]:
            raise ValueError("Input JPEG changed "+row["name"])
    case_cfg=CASES[case]
    start=dt.datetime.fromisoformat(case_cfg["from_utc"].replace("Z","+00:00"))
    end=dt.datetime.fromisoformat(case_cfg["through_utc"].replace("Z","+00:00"))
    frames,times,sizes=h.load_frames(
        cache,camera,end,hours=(end-start).total_seconds()/3600+.01)
    if len(frames)!=len(good):
        raise ValueError("Frame loading discrepancy")
    if [h.iso(t) for t in times]!=manifest["processed_times_utc"]:
        raise ValueError("Frame timestamps differ from baseline")
    full,stats=scan_without_early_return(
        frames,times,sizes,camera,max_seed_pairs=max_seed_pairs,
        max_distinct_tracks=max_tracks)
    parity=compare_prefix(full,manifest)
    ranked=sorted(full,key=h.track_quality,reverse=True)
    original_ids={t["id"] for t in manifest["ranked_tracks"]}
    novel=[(rank,t) for rank,t in enumerate(ranked,1) if t["id"] not in original_ids]
    report={
        "mode":"OFFLINE_CCOR_POSITION_BLIND_CAP_FREE_SEED_ENUMERATION",
        "case":case,"camera":camera,
        "source":"NOAA CCOR L3 JPEG",
        "production_detector_code_sha256":sha,
        "production_modified":False,"network_access":False,
        "reported_comet_recovered":None,
        "frame_count":len(frames),
        "baseline_tracks":len(manifest["ranked_tracks"]),
        "baseline_prefix_matches_actual_production":parity,
        "search_stats":stats,
        "total_unique_tracks":len(full),
        "additional_distinct_track_ids":len(novel),
        "new_candidates_in_top_40_after_global_ranking":sum(
            row["id"] not in original_ids for row in ranked[:40]),
        "top20":[{"rank":i,"id":row["id"],
                   "frames":row["frames"],"rms_px":row["rms_px"],
                   "in_original_cap_40":row["id"] in original_ids,
                   "first":row["first"],"last":row["last"]}
                 for i,row in enumerate(ranked[:20],1)],
        "ranked_tracks":[{**row,"blind_rank":i} for i,row in enumerate(ranked,1)],
        "status":"COMPLETE_AND_PARITY_VALIDATED" if (
            stats["search_complete"] and parity) else (
            "INCOMPLETE_BUDGET" if not stats["search_complete"]
            else "PARITY_MISMATCH"),
        "limitations":[
            "Production peaks per-frame 70 point cap remains active.",
            "Comet report coordinates are NEVER used to generate these tracks.",
            "Comet identification needs independent FITS/JPEG registration.",
            "Multiple candidate tracks may describe artifacts, not real objects."
        ],
    }
    output_dir.mkdir(parents=True,exist_ok=True)
    dest=output_dir/("blind_uncapped_"+case+".json")
    dest.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(f"{case}: {len(frames)} JPEG frames | baseline 40-cap "
          f"{report['baseline_tracks']} | all distinct {len(full)} "
          f"| new IDs {len(novel)} | new top40 "
          f"{report['new_candidates_in_top_40_after_global_ranking']} "
          f"| first40 parity {parity} | {report['status']}",flush=True)
    print("  Seed pairs:",stats["seed_pairs_tested"],
          "| passing speed:",stats["seed_pairs_passing_speed"],
          "| accepted tracks:",stats["seed_pairs_passing_track_shape"],flush=True)
    print("  Saved:",dest,flush=True)
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-dir",type=Path,required=True)
    ap.add_argument("--output-dir",type=Path,required=True)
    ap.add_argument("--case",choices=tuple(CASES),action="append")
    ap.add_argument("--max-seed-pairs",type=int,default=150000)
    ap.add_argument("--max-tracks",type=int,default=5000)
    args=ap.parse_args()
    source=args.source_dir.expanduser().resolve()
    output=args.output_dir.expanduser().resolve()
    if output==source or output in source.parents or source in output.parents:
        ap.error("Output must be separate from the baseline and its descendants")
    if not (1000<=args.max_seed_pairs<=1_000_000):
        ap.error("max-seed-pairs must be 1000..1000000")
    if not (100<=args.max_tracks<=20000):
        ap.error("max-tracks must be 100..20000")
    fail=False
    for case in dict.fromkeys(args.case or CASES):
        row=run_one(case,source,output,args.max_seed_pairs,args.max_tracks)
        fail |= row["status"]!="COMPLETE_AND_PARITY_VALIDATED"
    if fail:
        raise SystemExit("One or more searches incomplete or failed parity. Inspect JSON.")
    print("Offline complete seed enumeration succeeded without changing production.")


if __name__=="__main__":
    main()
