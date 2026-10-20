#!/usr/bin/env python3
"""Read-only A/B audit: does a persistent dark streak crowd top C3 candidates?

Uses EXISTING cached 512px SOHO frames + latest complete CUDA logs. No network,
GPU, changes to the realtime runner, candidate files, mode flags or dashboard.
Candidates are NOT deleted: the alternative ordering is diagnostic only.

Tests native CUDA score ordering vs:
 (a) suspected persistent shadow/edge association relegated to lower priority,
 (b) spatial-diverse track ordering, and (c) both.
The association is strictly spatial; it is NOT a definitive classification.
Do NOT deploy penalties until tested on known comets from real 512 C3 frames.

  python3 tools/audit_c3_shadow_ranking.py \
    --project ~/comethunting/comet_hunter_native_cuda_v7_archive
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path

import numpy as np

from diagnose_c3_shadow_streak import (
    SIZE, WINDOWS, shadow_masks, load_gray, choose_evenly,
    latest_complete_logs
)
from realtime_hunter import (
    parse_candidates, candidate_quadrant, comet_filter_reasons, image_time
)

QUADRANTS=("UPPER_LEFT","UPPER_RIGHT","LOWER_LEFT","LOWER_RIGHT","UNKNOWN")
SHIFTS=(-48,-24,24,48)


def _in_field(xx, yy, quadrant):
    valid=(xx>=24)&(xx<SIZE-24)&(yy>=24)&(yy<SIZE-24)
    if quadrant=="LOWER_LEFT":
        valid &= (xx<256)&(yy>=256)
    elif quadrant=="UPPER_LEFT":
        valid &= (xx<256)&(yy<256)
    elif quadrant=="UPPER_RIGHT":
        valid &= (xx>=256)&(yy<256)
    elif quadrant=="LOWER_RIGHT":
        valid &= (xx>=256)&(yy>=256)
    return valid


def track_artifact_evidence(track, feature, sample_count=21,
                            min_samples=8, offsets=SHIFTS):
    """Paired spatial-control evidence on identical positions within a quadrant.

    Each shifted control is compared with the real path on exactly the sample
    locations where BOTH are valid. This avoids inflated contrasts due to
    unequal valid path portions near sector/image boundaries.
    """
    quadrant=candidate_quadrant(track)
    first,last=track["first"],track["last"]
    xs=np.linspace(first["x"],last["x"],sample_count)
    ys=np.linspace(first["y"],last["y"],sample_count)
    x=np.rint(xs).astype(int)
    y=np.rint(ys).astype(int)
    valid=_in_field(x,y,quadrant)
    if valid.sum()<min_samples:
        return {"evaluable":False,"associated_with_persistent_shadow":False,
                "reason":"INSUFFICIENT_PATH_PIXELS"}
    controls=[]
    matching=[]
    for offset in offsets:
        cx=x+offset
        pairs=valid&_in_field(cx,y,quadrant)
        if pairs.sum()<min_samples:
            continue
        actual=float(np.mean(feature[y[pairs],x[pairs]]))
        null=float(np.mean(feature[y[pairs],cx[pairs]]))
        matching.append(actual)
        controls.append(null)
    if len(controls)<2:
        return {"evaluable":False,"associated_with_persistent_shadow":False,
                "reason":"INSUFFICIENT_MATCHED_CONTROLS"}
    observed=float(np.mean(matching))
    comparison=float(np.mean(controls))
    excess=observed-comparison
    # Two independent constraints: a track must spend much of its path ON the
    # persistent feature, and controls must be substantially less associated.
    associated=bool(observed>=0.50 and excess>=0.30)
    return {
        "evaluable":True,
        "path_contact_fraction":round(observed,4),
        "control_contact_fraction":round(comparison,4),
        "excess_fraction":round(excess,4),
        "control_paths":len(controls),
        "associated_with_persistent_shadow":associated,
    }


def diversify(items, cells=8, max_per_cell=3):
    """Apply previous unsupervised rank-diversity proposal without deletion."""
    if cells<2 or max_per_cell<1:
        raise ValueError("Invalid diversification parameters")
    admitted=[]
    overflow=[]
    occupancy={}
    for item in items:
        t=item["track"]
        x=(t["first"]["x"]+t["last"]["x"])/2.
        y=(t["first"]["y"]+t["last"]["y"])/2.
        index=(max(0,min(cells-1,int(x*cells/SIZE))),
               max(0,min(cells-1,int(y*cells/SIZE))))
        if occupancy.get(index,0)<max_per_cell:
            occupancy[index]=occupancy.get(index,0)+1
            admitted.append(item)
        else:
            overflow.append(item)
    return admitted+overflow


def ranking_order(items, policy):
    original=sorted(items,key=lambda i:(-float(i["track"]["score"]),i["native_rank"]))
    if policy=="native":return original
    if policy=="streak_last":
        return sorted(original,key=lambda i:bool(i["shadow"].get(
            "associated_with_persistent_shadow",False)))
    if policy=="spatial_diversity":
        return diversify(original)
    if policy=="streak_then_diverse":
        return diversify(ranking_order(items,"streak_last"))
    raise ValueError(policy)


def top_counts(ordered,k):
    top=ordered[:k]
    counts={q:0 for q in QUADRANTS}
    for entry in top:
        q=entry["quadrant"]
        counts[q]=counts.get(q,0)+1
    return {
        "total":len(top),
        "per_quadrant":counts,
        "shadow_associated":sum(i["shadow"].get(
            "associated_with_persistent_shadow",False) for i in top),
        "comet_like":sum(i["comet_like"] for i in top),
    }


def audit(tracks_by_window,feature,limit=100):
    result={}
    for hours,tracks in tracks_by_window.items():
        items=[]
        for index,t in enumerate(tracks,1):
            quadrant=candidate_quadrant(t)
            shadow=track_artifact_evidence(t,feature)
            items.append({"track":t,"quadrant":quadrant,"native_rank":index,
                          "shadow":shadow,
                          "comet_like":not comet_filter_reasons(t)})
        nflag=sum(i["shadow"].get("associated_with_persistent_shadow",False)
                  for i in items)
        byquadrant={q:{
            "total":sum(i["quadrant"]==q for i in items),
            "shadow_associated":sum(i["quadrant"]==q and
                i["shadow"].get("associated_with_persistent_shadow",False) for i in items),
        } for q in QUADRANTS}
        policies={p:top_counts(ranking_order(items,p),limit) for p in
                  ("native","streak_last","spatial_diversity","streak_then_diverse")}
        result[str(hours)]={
            "tracks":len(items),
            "evaluable":sum(i["shadow"].get("evaluable",False) for i in items),
            "shadow_associated_total":nflag,
            "comet_like_total":sum(i["comet_like"] for i in items),
            "quadrants":byquadrant,
            "top_k":limit,
            "policies":policies,
        }
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project",required=True,type=Path)
    p.add_argument("--samples",default=15,type=int)
    p.add_argument("--top-k",default=100,type=int)
    p.add_argument("--output",type=Path)
    args=p.parse_args()
    if not 5<=args.samples<=40:
        p.error("--samples must be 5..40")
    if not 10<=args.top_k<=500:
        p.error("--top-k must be 10..500")
    project=args.project.expanduser().resolve()
    root=project/"results"/"realtime"
    runid,logs=latest_complete_logs(root)
    bywindow={w:parse_candidates(path.read_text(encoding="utf-8",errors="replace"))
              for w,path in logs.items()}
    cache=Path("/dev/shm")/f"comet_realtime_{os.environ.get('USER','user')}_c3_512"
    if not cache.is_dir():
        raise SystemExit("Realtime image cache not found; no images downloaded")
    latest=root/"last_detector_frame.txt"
    # Freeze diagnostic imagery at or before the native detector's last image.
    last_name=latest.read_text().strip() if latest.is_file() else ""
    if not last_name:
        raise SystemExit("Missing last_detector_frame.txt; cannot bound image analysis")
    cutoff=image_time(last_name)
    files=[x for x in sorted(cache.glob("*_c3_512.jpg"))
           if image_time(x.name)<=cutoff]
    selected=choose_evenly(files,args.samples)
    if len(selected)<5:
        raise SystemExit("Fewer than five matched original images available")
    print(f"Auditing CUDA run {runid} with {len(selected)} historical 512px images "
          f"up to {last_name}. No new SOHO requests.",flush=True)
    samples=[]
    for i,path in enumerate(selected,1):
        samples.append(load_gray(path))
        print(f"  decoded {i}/{len(selected)} {path.name}",flush=True)
    shadow,edge,_=shadow_masks(np.stack(samples))
    combined=shadow|edge
    results=audit(bywindow,combined,args.top_k)
    payload={
        "run_id":runid,
        "last_detector_frame":last_name,
        "image_samples":[f.name for f in selected],
        "resolution":SIZE,
        "shadow_coverage_lower_left_pct":round(
            float(combined[256:488,24:256].mean())*100,2),
        "shadow_coverage_upper_right_pct":round(
            float(combined[24:256,256:488].mean())*100,2),
        "ranking":"offline stable reordered output only; no CUDA search or production change",
        "important_caveat":"Shadow association cannot prove a false comet; proposed rank changes are unvalidated against independent real-comet recall.",
        "window_results":results,
    }
    output=(args.output or root/"shadow_candidate_ranking_audit.json").expanduser().resolve()
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
    for window,row in results.items():
        print(f"\n=== {window}h CUDA search; {row['tracks']} tracks ===",flush=True)
        print("Persistent shadow association:",
              row["shadow_associated_total"],"tracks;",
              row["evaluable"],"evaluable")
        for q in QUADRANTS[:4]:
            v=row["quadrants"][q]
            print(f"  {q}: {v['shadow_associated']}/{v['total']} flagged")
        for name,r in row["policies"].items():
            print(f"  {name:<21} top{args.top_k}: "
                  f"LL={r['per_quadrant']['LOWER_LEFT']}, "
                  f"shadow-associated={r['shadow_associated']}, "
                  f"comet-like={r['comet_like']}")
    print("\nWritten:",output)
    print("No native CUDA changes. Not safe to deploy any rank penalty until "
          "known-comet sensitivity and 512px genuine-motion control are checked.")


if __name__=="__main__":
    main()
