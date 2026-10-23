#!/usr/bin/env python3
"""Position-blind multi-window diagnostic of the REAL production CCOR detector.

Production track_points returns immediately on finding 40 hypotheses. Because
its loops search seed frames in time order, an all-frame run can return just
early-seed hypotheses. This isolated experiment runs UNMODIFIED track_points
on overlapping time slices, then de-duplicates results and ranks only at end.

Importantly the 40-per-window cap STILL applies: counts are not exhaustive;
this test does not change the live hunter or prove reported-comet recovery.
Only previously validated historical JPEGs are used. No network calls.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from collections import defaultdict

import ccor_hunter as hunter
from ccor_blind_jpeg_replay import CASES, detector_fingerprint, validate_jpeg

SIZES=(7,9,11,13)
STRIDE=2


def windows(n, sizes=SIZES, stride=STRIDE):
    """Distinct time windows, ensure the final frames are represented."""
    planned=set()
    for length in sizes:
        if length>n:
            continue
        starts=list(range(0,n-length+1,stride))
        if n-length not in starts:
            starts.append(n-length)
        planned.update((i,i+length) for i in starts)
    return sorted(planned)


def deduplicate(tracks):
    """Same-camera production identity matching, keeping best-quality track."""
    ranked=sorted(tracks,key=hunter.track_quality,reverse=True)
    retained=[]
    for candidate in ranked:
        if any(hunter.match_track(older,candidate,None,None) for older in retained):
            continue
        retained.append(candidate)
    return retained


def sweep_case(case_name,source_dir,output_dir, *,
               sizes=SIZES,stride=STRIDE, revalidate=True):
    output_dir.mkdir(parents=True,exist_ok=True)
    source=source_dir/("blind_jpeg_"+case_name+".json")
    if not source.is_file():
        raise FileNotFoundError("First run ccor_blind_jpeg_replay: "+str(source))
    reference=json.loads(source.read_text(encoding="utf-8"))
    if reference.get("status")!="POSITION_BLIND_REPLAY_COMPLETE":
        raise ValueError("Baseline replay did not complete for "+case_name)
    camera=CASES[case_name]["camera"]
    if reference.get("camera")!=camera or not reference.get("blind_to_report_positions"):
        raise ValueError("Invalid or non-blind reference manifest")
    original_sha=reference.get("detector_source_sha256")
    current_sha=detector_fingerprint()
    if original_sha!=current_sha:
        raise ValueError("Production detector changed since original blind run. Do not silently compare versions.")
    cache=source_dir/case_name/"jpegs"
    entries=[e for e in reference.get("images",[]) if e.get("status")=="VALIDATED"]
    if not entries or len(entries)!=reference.get("frame_count"):
        raise ValueError("Baseline JPEG count differs from processed count")
    if revalidate:
        for entry in entries:
            path=cache/entry["name"]
            details=validate_jpeg(path)
            if entry.get("sha256")!=details["sha256"]:
                raise ValueError("Reference JPEG changed: "+entry["name"])
    from_utc=dt.datetime.fromisoformat(CASES[case_name]["from_utc"].replace("Z","+00:00"))
    to_utc=dt.datetime.fromisoformat(CASES[case_name]["through_utc"].replace("Z","+00:00"))
    frames,times,sizes_original=hunter.load_frames(
        cache,camera,to_utc,hours=(to_utc-from_utc).total_seconds()/3600+.01)
    if len(frames)!=len(entries):
        raise ValueError("Actual processed JPEG count differs from baseline")
    if [hunter.iso(t) for t in times]!=reference.get("processed_times_utc"):
        raise ValueError("JPEG timestamps do not match initial blind run")
    diagnostics=[]
    candidates=[]
    for first,last in windows(len(frames),sizes=sizes,stride=stride):
        rows=hunter.track_points(frames[first:last],times[first:last],
                                 sizes_original[first:last],camera)
        diagnostics.append({
            "first_frame_utc":hunter.iso(times[first]),
            "last_frame_utc":hunter.iso(times[last-1]),
            "frames":last-first,
            "tracks_returned":len(rows),
            "hit_40_track_cap":len(rows)==40
        })
        for item in rows:
            candidate=dict(item)
            candidate["source_window_first"]=hunter.iso(times[first])
            candidate["source_window_last"]=hunter.iso(times[last-1])
            candidates.append(candidate)
    deduped=deduplicate(candidates)
    baseline=reference.get("ranked_tracks",[])
    report={
        "mode":"POSITION_BLIND_MULTIWINDOW_ACTUAL_CCOR_DETECTOR",
        "case":case_name,"camera":camera,
        "blind_to_report_positions":True,
        "blind_to_report_times":False,
        "production_modified":False,"no_network":True,
        "detector_source_sha256":current_sha,
        "frame_count":len(frames),
        "baseline_allframe_track_count":len(baseline),
        "baseline_hit_40_track_cap":len(baseline)>=40,
        "window_count":len(diagnostics),
        "windows_hitting_cap":sum(d["hit_40_track_cap"] for d in diagnostics),
        "raw_window_track_count":len(candidates),
        "distinct_window_tracks":len(deduped),
        "distinct_not_in_baseline":sum(not any(
            hunter.match_track(b,row,None,None) for b in baseline)
            for row in deduped),
        "windows":diagnostics,
        "ranked_tracks":[{**candidate,"blind_rank":i+1}
                         for i,candidate in enumerate(deduped)],
        "reported_comet_recovered":None,
        "warning":"Repeated capped detector on overlapping subsets; not an exhaustive search or validated comet recovery."
    }
    destination=output_dir/("blind_sweep_"+case_name+".json")
    destination.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(f"{case_name}: {len(frames)} JPEG frames, {len(diagnostics)} windows, "
          f"{len(candidates)} window tracks, {len(deduped)} distinct tracks; "
          f"{report['windows_hitting_cap']} windows hit 40-cap; "
          f"{report['distinct_not_in_baseline']} distinct not in original 40",flush=True)
    print("  Saved:",destination,flush=True)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-dir",required=True,type=Path,
                   help="Output directory from position-blind historical replay")
    p.add_argument("--output-dir",required=True,type=Path,
                   help="Separate results directory; MUST NOT be a live hunter results path")
    p.add_argument("--case",action="append",choices=tuple(CASES))
    args=p.parse_args()
    source=args.source_dir.expanduser().resolve()
    output=args.output_dir.expanduser().resolve()
    if output==source:
        p.error("Output directory must differ from baseline source directory")
    if source in output.parents or output in source.parents:
        # Avoid overwriting or writing beneath the source input tree.
        p.error("Choose a separate non-nested output directory")
    for case in dict.fromkeys(args.case or CASES):
        sweep_case(case,source,output)
    print("Completed blind multi-window cap diagnostic; no production files touched.")


if __name__=="__main__":
    main()
