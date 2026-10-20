#!/usr/bin/env python3
"""REAL independently labelled C3 comet recovery at the LIVE 512px resolution.

Replays the SAME enhanced flags as the deployed live hunter, but on resized
OFFICIAL 1024x1024 Sungrazer practice GIFs with independently labelled x/y.
This is NOT proof of recall on all real completed archive images.

All input ZIPs must already be present. No SOHO/Internet access, writes to
production state, or replacement of any CUDA binary. Stages JPEGs only in an
auto-removed /dev/shm directory. Uses GPU only with --execute and refuses to
compete with an existing comet_hunter_archive process.

Run PLAN first:
    python3 tools/benchmark_official_c3_512.py --project \
      ~/comethunting/comet_hunter_native_cuda_v7_archive

Then on Bourbaki WHEN THE LIVE CUDA detector is idle:
    python3 tools/benchmark_official_c3_512.py --project \
      ~/comethunting/comet_hunter_native_cuda_v7_archive --execute

Preserves prior 1024 practice benchmarks in separate output directory:
    results/sungrazer_c3_512_truth_replay/
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
import zipfile

from benchmark_known_comets import busy_cuda, parse_output
from benchmark_official_practice_c3 import identify_c3_training, match_comet

CONTROL_NAMES = ("soho732", "soho1264", "soho1510")
FLAGS = (
    "--within-window-stationary-filter",
    "--spatial-balanced-peaks",
    "--trajectory-integrity-filter",
    "--trajectory-null-control",
)
REQUIRED_OPTS = ("--spatial-balanced-peaks","--trajectory-null-control",
                 "--trajectory-integrity-filter")


def truth_at_512(selected):
    """Independently labelled native 1024 coordinates scaled by EXACTLY two."""
    result = []
    for _,point in selected:
        result.append({
            "time": point["time"].replace(second=0,microsecond=0),
            "x":float(point["x"])/2.,
            "y":float(point["y"])/2.,
        })
    return result


def stage_512(zpath, selected, dest):
    """512 area reduction, JPEG q2. Never alter original practice ZIP/GIFs."""
    completed = []
    with zipfile.ZipFile(zpath) as archive:
        for member,point in selected:
            stamp=point["time"].strftime("%Y%m%d_%H%M")
            target=dest/f"{stamp}_c3_512.jpg"
            if target.exists():
                raise ValueError(f"Duplicate independently labelled timestamp {stamp}")
            raw=archive.read(member)
            # GIF data on stdin, first frame only, no persistent originals.
            # ffmpeg area interpolation is a 2x2 aggregate, not upscaling.
            proc=subprocess.run([
                "ffmpeg","-hide_banner","-loglevel","error","-y",
                "-i","pipe:0","-frames:v","1",
                "-vf","scale=512:512:flags=area",
                "-q:v","2",str(target)
            ],input=raw,capture_output=True,timeout=50)
            if proc.returncode or not target.exists() or target.stat().st_size<2000:
                raise RuntimeError("512px image staging failed: "+
                                   proc.stderr.decode(errors="replace")[-250:])
            completed.append(target)
    return completed


def evaluate(tracks,truth,cap):
    # All thresholds are in tested-resolution pixels. The original published
    # practice benchmark used 12px median / 22px max at 1024 resolution.
    # Exactly half those tolerances are appropriate for downscaled 512 input.
    result=match_comet(tracks,truth,max_median_px=6.0,max_worst_px=11.0)
    top100=match_comet(tracks[:100],truth,max_median_px=6.0,max_worst_px=11.0)
    first=result["best_match"]
    return {
        "candidate_count":len(tracks),
        "cap_hit":len(tracks)>=cap,
        "known_comet_recovered_in_full_pool":result["comet_recovered"],
        "known_comet_recovered_in_top_100":top100["comet_recovered"],
        "first_matched_track":first["candidate"] if first else None,
        "first_matched_track_rank":first["original_rank"] if first else None,
        "first_matched_track_priority":first["priority"] if first else None,
        "median_position_error_512_px":first["median_error_px"] if first else None,
        "worst_position_error_512_px":first["worst_error_px"] if first else None,
        "labelled_positions_covered":first["truth_observations_covered"] if first else None,
        "closest_eligible_track":result["closest_track"],
        "independent_truth_positions":len(truth),
        "matching_tolerances_512_px":{"median":6,"worst":11},
    }


def main():
    p=argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project",required=True,type=Path)
    p.add_argument("--execute",action="store_true",
                   help="Run existing installed CUDA binary only when GPU idle")
    p.add_argument("--case",choices=CONTROL_NAMES,action="append",
                   help="One or more known cases; default all three")
    p.add_argument("--budget-minutes",type=int,default=2)
    p.add_argument("--max-candidates",type=int,default=500)
    p.add_argument("--binary",type=Path,
                   help="Default project's existing production CUDA executable")
    p.add_argument("--output",type=Path)
    args=p.parse_args()
    if not 1<=args.budget_minutes<=5:
        p.error("--budget-minutes must be 1..5")
    if not 100<=args.max_candidates<=1000:
        p.error("--max-candidates must be 100..1000")
    project=args.project.expanduser().resolve()
    root=project/"results"/"official_sungrazer_controls"
    output=(args.output or project/"results"/"sungrazer_c3_512_truth_replay").expanduser().resolve()
    cases=[]
    for name in dict.fromkeys(args.case or CONTROL_NAMES):
        archive=root/f"{name}.zip"
        if not archive.is_file():
            print(f"{name}: official ZIP missing ({archive})",flush=True)
            continue
        record=identify_c3_training(archive)
        if record["matched_c3_gifs"]<5:
            print(f"{name}: fewer than five independent C3 observations",flush=True)
            continue
        cases.append((name,archive,record["selected"]))
        print(f"READY: {name} — {record['matched_c3_gifs']} official C3 GIFs; "
              "independent 1024 x/y → native 512 x/y",flush=True)
    if not cases:
        raise SystemExit("No usable offline real-comet controls; nothing changed")
    if not args.execute:
        print("PLAN ONLY: pass --execute when GPU is idle; original ZIPs untouched.")
        return
    binary=(args.binary or project/"comet_hunter_archive").expanduser().resolve()
    if not binary.is_file():
        raise SystemExit(f"CUDA binary unavailable: {binary}")
    opts=subprocess.run([str(binary),"--help"],capture_output=True,text=True,
                        timeout=15,check=True).stdout
    for option in REQUIRED_OPTS:
        if option not in opts:
            raise SystemExit(f"Existing CUDA binary lacks {option}; refusing test")
    output.mkdir(parents=True,exist_ok=True)
    report={
        "source":"Independent official Sungrazer C3 practice ZIPs",
        "simulation_resolution":512,
        "truth_resolution":512,
        "original_GIF_resolution":1024,
        "preprocessing":"original GIF first frame => area-downsample to 512 => JPEG q2",
        "enhanced_flags":list(FLAGS),
        "binary":str(binary),
        "candidate_cap":args.max_candidates,
        "budget_minutes":args.budget_minutes,
        "warning":"Not the same as the current SOHO completed 512 JPEG feed; may not predict recall on all real images",
        "production_changes":False,
        "cases":[],
    }
    for name,archive,selected in cases:
        if busy_cuda():
            print("LIVE CUDA IS BUSY: stopping replay without GPU competition.",flush=True)
            report["cases"].append({"dataset":name,"status":"GPU_BUSY"})
            break
        info={"dataset":name,"status":"PENDING"}
        report["cases"].append(info)
        print(f"\n=== Official {name} 512px enhanced CUDA replay ===",flush=True)
        with tempfile.TemporaryDirectory(prefix="comet_truth_512_",dir="/dev/shm") as temp:
            stage=Path(temp)
            staged=stage_512(archive,selected,stage)
            if len(staged)<5:
                info.update({"status":"INSUFFICIENT_FRAMES"})
                continue
            if busy_cuda():
                print("LIVE CUDA became busy during staging; skipping this dataset.")
                info.update({"status":"GPU_BUSY"})
                break
            cmd=[str(binary),"--camera","c3","--resolution","512",
                 "--input-dir",str(stage),"--budget-minutes",str(args.budget_minutes),
                 "--min-frames","5","--max-candidates",str(args.max_candidates),
                 *FLAGS]
            log_path=output/f"{name}_enhanced_512.log"
            start=time.monotonic()
            try:
                proc=subprocess.run(cmd,cwd=project,text=True,capture_output=True,
                                    timeout=max(300,args.budget_minutes*180))
            except subprocess.TimeoutExpired:
                info.update({"status":"CUDA_TIMEOUT"})
                break
            runtime=round(time.monotonic()-start,1)
            log_path.write_text(proc.stdout+"\n"+proc.stderr,encoding="utf-8")
            if proc.returncode:
                info.update({"status":"CUDA_ERROR","exit_code":proc.returncode,
                             "log":str(log_path)})
                print(f"{name}: CUDA error, log: {log_path}",flush=True)
                break
            tracks=parse_output(proc.stdout)
            stats=evaluate(tracks,truth_at_512(selected),args.max_candidates)
            info.update(stats)
            info.update({"status":"OK","runtime_s":runtime,"log":str(log_path),
                         "source_c3_frames":len(staged)})
            print(json.dumps(info,indent=2),flush=True)
        (output/"report_512.json").write_text(json.dumps(report,indent=2)+"\n")
    (output/"report_512.json").write_text(json.dumps(report,indent=2)+"\n")
    print("\nReport:",output/"report_512.json",flush=True)
    print("Not evidence of a new comet; use as a real-positive control BEFORE any artifact rejection.")


if __name__=="__main__":
    main()
