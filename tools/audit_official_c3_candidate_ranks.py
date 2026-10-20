#!/usr/bin/env python3
"""OFFLINE candidate-ranking audit on existing REAL known-C3 CUDA log files.

This script does NOT train ranking from official comet positions. Every proposed
ordering is solely a function of the detector's reported score, frame count,
and track location. Only after ranking do we compare against independent
Sungrazer positional labels. No GPU, no downloading, no production changes.

Input logs must have been produced by --spatial-balanced-peaks and
--trajectory-null-control with the SAME --max-candidates=500.
The results measure offline top-100 selection from the 500-track pool. They
are NOT equivalent to rerunning CUDA at --max-candidates=100, because internal
pass/global capping can alter the pool itself.

Command:
  python3 tools/audit_official_c3_candidate_ranks.py \
    --project ~/comethunting/comet_hunter_native_cuda_v7_archive
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from benchmark_known_comets import parse_output
from benchmark_official_practice_c3 import identify_c3_training, match_comet

DATASETS = ("soho732", "soho1264", "soho1510")
VARIANT = "spatial_and_null"


def labelled_truth(archive):
    case = identify_c3_training(archive)
    if case["matched_c3_gifs"] < 5:
        raise ValueError(f"{archive.name}: no suitable C3 labelled imagery")
    # The benchmark stages YYYYMMDD_HHMM file names, so model positions use
    # that exact minute precision rather than seconds in the reference text.
    result = []
    for _, row in case["selected"]:
        time = row["time"].replace(second=0,microsecond=0)
        result.append({"time":time,"x":row["x"],"y":row["y"]})
    return result


def rerank(tracks, policy="baseline", tile_count=8):
    """Stable, fully unsupervised ranking; no dataset or comet truth in scores."""
    if policy == "baseline":
        return tracks[:]

    def score(c):
        original = float(c["score"])
        if policy == "cap_frames_12":
            return original - 12.0 * max(0,int(c["frames"])-12)
        if policy == "cap_frames_20":
            return original - 12.0 * max(0,int(c["frames"])-20)
        if policy == "cap_frames_30":
            return original - 12.0 * max(0,int(c["frames"])-30)
        if policy == "rms_weighted":
            return original - 50.0 * min(4.0,max(0.0,float(c["rms"])))
        if policy == "cap_frames_20_rms":
            return (original-12.0*max(0,int(c["frames"])-20)
                    -30.0*min(4.0,max(0.0,float(c["rms"]))))
        if policy == "spatial_diversity":
            return original
        raise ValueError(f"Unknown ranking policy: {policy}")

    ordered=sorted(enumerate(tracks),key=lambda ic:(-score(ic[1]),ic[0]))
    ordered=[row for _,row in ordered]
    if policy != "spatial_diversity":
        return ordered

    # Track-level diversification. Select first three (strongest) tracks per
    # 8x8 sector, then fill remaining capacity from globally strongest tracks.
    # Sector limit is applied to the first 100 slots, not the whole 500-pool.
    selected, remaining = [], []
    counts={}
    for c in ordered:
        start, end=c["first"],c["last"]
        x=(start["x"]+end["x"])*0.5
        y=(start["y"]+end["y"])*0.5
        gx=max(0,min(tile_count-1,int(x*tile_count/1024.0)))
        gy=max(0,min(tile_count-1,int(y*tile_count/1024.0)))
        key=(gx,gy)
        if counts.get(key,0)<3:
            counts[key]=counts.get(key,0)+1
            selected.append(c)
        else:
            remaining.append(c)
    return selected+remaining


def inspect_run(rows,truth,k):
    eligible=rows[:k]
    matched=match_comet(eligible,truth)
    result={
        "retrieved_top_k":len(eligible),
        "comet_recovered":matched["comet_recovered"],
        "matched_candidate":matched["best_match"]["candidate"]
                            if matched["best_match"] else None,
        "original_output_rank":None,
        "new_rank":None,
        "median_error_px":matched["best_match"]["median_error_px"]
                          if matched["best_match"] else None,
        "worst_error_px":matched["best_match"]["worst_error_px"]
                         if matched["best_match"] else None,
        "frames":matched["best_match"]["frames"]
                 if matched["best_match"] else None,
    }
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project",type=Path,required=True)
    parser.add_argument("--limit",type=int,default=100)
    parser.add_argument("--pool",type=int,default=500,
                        help="Require this many returned tracks to avoid unequal-pool comparisons")
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    if not 10<=args.limit<=args.pool<=1000:
        parser.error("Require 10 <= limit <= pool <= 1000")

    project=args.project.expanduser().resolve()
    logdir=project/"results"/"sungrazer_c3_truth_replay"
    originals=project/"results"/"official_sungrazer_controls"
    policies=("baseline","cap_frames_12","cap_frames_20","cap_frames_30",
              "rms_weighted","cap_frames_20_rms","spatial_diversity")
    report={"source":"Sungrazer official independent C3 practice positions",
            "dataset_count":len(DATASETS),
            "variant":VARIANT,
            "pool":args.pool,"top_k":args.limit,
            "warning":"Offline reranking of existing 500-output pool; NOT a new CUDA 100-limit run, and not independently held-out ranking validation.",
            "results":[]}

    for name in DATASETS:
        log=logdir/f"{name}_{VARIANT}.log"
        archive=originals/f"{name}.zip"
        if not log.is_file() or not archive.is_file():
            print(name+": MISSING saved 500-track CUDA log or official ZIP",flush=True)
            report["results"].append({"dataset":name,"status":"MISSING_INPUT"})
            continue
        tracks=parse_output(log.read_text(encoding="utf-8",errors="replace"))
        if len(tracks)<args.pool:
            print(f"{name}: only {len(tracks)} tracks in saved log; "
                  f"need >= {args.pool}. Skipping unequal-pool comparison.",flush=True)
            report["results"].append({
                "dataset":name,"status":"INSUFFICIENT_POOL","tracks":len(tracks)})
            continue
        tracks=tracks[:args.pool]
        truth=labelled_truth(archive)
        print(f"\n{name} | {len(tracks)} candidates | {len(truth)} official C3 positions")
        item={"dataset":name,"status":"OK","truth_points":len(truth),"policies":[]}
        report["results"].append(item)
        index={row["id"]:i+1 for i,row in enumerate(tracks)}
        for policy in policies:
            ranked=rerank(tracks,policy)
            matched=match_comet(ranked[:args.limit],truth)
            anymatch=match_comet(ranked,truth)
            best=matched["best_match"]
            first_any=anymatch["best_match"]
            entry={
                "policy":policy,
                "top_k_recovered":matched["comet_recovered"],
                "full_pool_recovered":anymatch["comet_recovered"],
                "top_k_candidate":best["candidate"] if best else None,
                "original_rank":index[best["candidate"]] if best else None,
                "new_rank":(next(i+1 for i,r in enumerate(ranked)
                                 if r["id"]==best["candidate"]) if best else None),
                "full_pool_best_id":first_any["candidate"] if first_any else None,
                "full_pool_best_original_rank":
                    index[first_any["candidate"]] if first_any else None,
                "full_pool_best_new_rank":
                    next((i+1 for i,r in enumerate(ranked)
                          if first_any and r["id"]==first_any["candidate"]),None),
                "full_pool_best_priority":(
                    next((r["priority"] for r in tracks
                          if first_any and r["id"]==first_any["candidate"]),None)),
                "median_error_px":first_any["median_error_px"] if first_any else None,
            }
            item["policies"].append(entry)
            print(f"  {policy:<23} top{args.limit}={str(entry['top_k_recovered']):<5} "
                  f"best positional rank {entry['full_pool_best_new_rank']} "
                  f"(original {entry['full_pool_best_original_rank']}); "
                  f"priority={entry['full_pool_best_priority']}",flush=True)

    dest=(args.output or logdir/"candidate_rank_audit.json").expanduser().resolve()
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print("\nAudit:",dest,flush=True)
    print("CAUTION: candidate selection on these three known objects may overfit. "
          "A promising ranking must be checked with new held-out comet cases "
          "and false-positive controls BEFORE CUDA adoption.",flush=True)


if __name__=="__main__":
    main()
