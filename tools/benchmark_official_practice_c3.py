#!/usr/bin/env python3
"""Offline independent REAL C3 comet recovery test from Sungrazer practice ZIPs.

Only image observations labelled C3 in the supplied positions.txt are used.
Positions are from the official training file, NOT the Comet Hunter dashboard.
No NASA/SOHO access, no CUDA binary replacement, and no production state writes.

The official practice images are GIFs. Their first frame is converted to JPEG
without up/down-scaling, to fit the CUDA JPEG input. Results are consequently
a calibrated practice-GIF replay, NOT a raw Completed-archive recall test.

First inventory all locally fetched datasets (default):
  python3 tools/benchmark_official_practice_c3.py --project ~/comethunting/comet_hunter_native_cuda_v7_archive

Then run on any archive that has >=5 C3-labelled images:
  python3 tools/benchmark_official_practice_c3.py --project ... --execute
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import zipfile

from benchmark_known_comets import busy_cuda, parse_output, when

CANDIDATE_ROW = re.compile(
    r"^\s*((?:19|20)\d{2})/(\d{2})/(\d{2})\s+"
    r"(\d{2}):(\d{2}):(\d{2})\s+"
    r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\b"
)
IMAGE_STAMP = re.compile(r"(?<!\d)(\d{6})_(\d{4})(?!\d)")
VARIANTS = {
    "stationary_only": ["--within-window-stationary-filter"],
    "plus_integrity": [
        "--within-window-stationary-filter",
        "--trajectory-integrity-filter"
    ],
    "plus_integrity_and_null": [
        "--within-window-stationary-filter",
        "--trajectory-integrity-filter",
        "--trajectory-null-control"
    ],
}


def parse_positions(text):
    """Use upper-left x/y, explicitly respecting per-line [c2] tags."""
    low = text.lower()
    if "upper-left" not in low:
        raise ValueError("Position-file origin not specified as upper-left")
    if "lasco c3" not in low:
        raise ValueError("Camera-default legend missing; cannot infer unlabelled rows")
    result = []
    for line in text.splitlines():
        m = CANDIDATE_ROW.match(line)
        if not m:
            continue
        yr, mo, day, hour, minute, second = map(int, m.groups()[:6])
        x, y = float(m.group(7)), float(m.group(8))
        stamp = dt.datetime(yr, mo, day, hour, minute, second, tzinfo=dt.timezone.utc)
        is_c2 = bool(re.search(r"\[\s*c2\s*\]", line, re.I))
        result.append({"time": stamp, "x": x, "y": y,
                       "camera": "c2" if is_c2 else "c3"})
    if not result:
        raise ValueError("No parseable DATE TIME X Y position rows")
    return sorted(result, key=lambda p: p["time"])


def image_minute(name):
    """Filename YYMMDD_HHMM (timestamps rounded down to minute in JPEG inputs)."""
    m = IMAGE_STAMP.search(Path(name).name)
    if not m:
        return None
    return dt.datetime.strptime(m.group(1)+m.group(2), "%y%m%d%H%M").replace(
        tzinfo=dt.timezone.utc
    )


def identify_c3_training(archive):
    """Select only GIF images with unambiguous independently labelled C3 truth."""
    with zipfile.ZipFile(archive) as z:
        txts = [n for n in z.namelist() if n.lower().endswith(".txt")
                and "position" in Path(n).name.lower()]
        if len(txts) != 1:
            raise ValueError(f"Expected one positions text file, found {txts}")
        position_file = txts[0]
        text = z.read(position_file).decode("utf-8", "replace")
        points = parse_positions(text)
        by_minute = {}
        for p in points:
            minute = p["time"].replace(second=0, microsecond=0)
            by_minute.setdefault(minute, []).append(p)
        selected = []
        ambiguous = []
        for info in z.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".gif"):
                continue
            t = image_minute(info.filename)
            if t is None:
                continue
            labelled = by_minute.get(t, [])
            if len(labelled) != 1:
                if len(labelled) > 1:
                    ambiguous.append(info.filename)
                continue
            truth = labelled[0]
            # Do NOT mistake the seven C2 frames of soho1561 for C3!
            if truth["camera"] != "c3":
                continue
            if "_c2_" in info.filename.lower():
                continue
            if info.file_size > 12_000_000:
                raise ValueError("Image too large for safe extraction")
            selected.append((info.filename, truth))
        selected.sort(key=lambda pair:pair[1]["time"])
        seconds = {p["time"].strftime("%Y-%m-%dT%H:%M:%SZ") for p in points}
        return {
            "dataset": archive.stem,
            "archive": str(archive),
            "positions_file": position_file,
            "positions_total": len(points),
            "positions_c2": sum(p["camera"]=="c2" for p in points),
            "positions_c3": sum(p["camera"]=="c3" for p in points),
            "matched_c3_gifs": len(selected),
            "ambiguous_timestamps": ambiguous,
            "selected": selected,
            "unique_position_times": len(seconds),
        }


def native_dimensions(path):
    r = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x", str(path)
    ], text=True, capture_output=True, check=True, timeout=30)
    return r.stdout.strip().splitlines()[0]


def convert_training_gifs(zpath, selected, stage):
    """Convert without resampling and stage standard CUDA C3 JPEG names."""
    truth = []
    with zipfile.ZipFile(zpath) as z:
        for member, point in selected:
            stamp = point["time"].strftime("%Y%m%d_%H%M")
            jpg = stage / f"{stamp}_c3_1024.jpg"
            gif = stage / f"{stamp}.source.gif"
            if jpg.exists():
                raise ValueError("Duplicate C3 minute; cannot safely stage frames")
            gif.write_bytes(z.read(member))
            try:
                dimensions = native_dimensions(gif)
                if dimensions != "1024x1024":
                    raise ValueError(
                        f"Practice image {member}: native GIF is {dimensions}, "
                        "not 1024x1024; refusing to resize")
                command = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(gif), "-frames:v", "1", "-q:v", "2", str(jpg)
                ]
                subprocess.run(command, check=True, capture_output=True,
                               text=True, timeout=45)
            finally:
                gif.unlink(missing_ok=True)
            if not jpg.exists() or jpg.stat().st_size < 10000:
                raise ValueError(f"GIF to JPEG conversion produced invalid image: {member}")
            truth.append({
                "time": when(jpg.name),
                "x": point["x"],
                "y": point["y"],
                "source_gif": member,
                "staged_jpeg": jpg.name,
            })
    return truth


def match_comet(tracks, truth, max_median_px=12.0, max_worst_px=22.0):
    """Match track endpoint trajectory against independent camera-aligned x/y.

    Reported endpoints must cover >=3 labelled timestamps and match actual
    positions. Temporal overlap alone never counts as recovery.
    """
    evaluated = []
    for c in tracks:
        first, last = c["first"], c["last"]
        elapsed = (last["time"]-first["time"]).total_seconds()
        if elapsed <= 0 or c["frames"] < 5:
            continue
        errs = []
        for p in truth:
            t = p["time"]
            if first["time"] <= t <= last["time"]:
                fraction = (t-first["time"]).total_seconds()/elapsed
                x = first["x"]+(last["x"]-first["x"])*fraction
                y = first["y"]+(last["y"]-first["y"])*fraction
                errs.append(math.hypot(x-p["x"], y-p["y"]))
        if len(errs) < 3:
            continue
        errs.sort()
        median = errs[len(errs)//2]
        worst = errs[-1]
        evaluated.append({
            "candidate": c["id"], "frames": c["frames"],
            "truth_observations_covered": len(errs),
            "median_error_px": round(median,2),
            "worst_error_px": round(worst,2),
            "match": median <= max_median_px and worst <= max_worst_px,
        })
    evaluated.sort(key=lambda v:(v["median_error_px"],v["worst_error_px"]))
    matches = [x for x in evaluated if x["match"]]
    return {
        "independent_positions": len(truth),
        "eligible_tracks": len(evaluated),
        "comet_recovered": bool(matches),
        "best_match": matches[0] if matches else None,
        "closest_track": evaluated[0] if evaluated else None,
        "tolerance_median_px": max_median_px,
        "tolerance_worst_px": max_worst_px,
    }


def run_cuda(binary, project, stage, minutes, max_candidates, flags, log):
    command = [
        str(binary), "--camera", "c3", "--resolution", "1024",
        "--input-dir", str(stage), "--budget-minutes", str(minutes),
        "--min-frames", "5", "--max-candidates", str(max_candidates),
    ] + list(flags)
    start=time.monotonic()
    process = subprocess.run(command, cwd=project, capture_output=True,
                             text=True, timeout=max(300, minutes*180))
    output=process.stdout+"\n"+process.stderr
    log.write_text(output, encoding="utf-8")
    return process.returncode, parse_output(output), time.monotonic()-start


def main():
    ap=argparse.ArgumentParser(description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project",required=True,type=Path)
    ap.add_argument("--archive",type=Path,action="append",
                    help="Local official Sungrazer ZIP(s); repeat if needed")
    ap.add_argument("--binary",type=Path,
                    help="Isolated test binary, defaults to existing trajectory_gate_ab build")
    ap.add_argument("--max-candidates",type=int,default=500)
    ap.add_argument("--budget-minutes",type=int,default=2)
    ap.add_argument("--execute",action="store_true")
    ap.add_argument("--include-spatial",action="store_true",
                    help="Also A/B test opt-in spatial peak balancing using actual known comet truth")
    ap.add_argument("--output",type=Path)
    args=ap.parse_args()
    if not 1<=args.budget_minutes<=10 or not 20<=args.max_candidates<=1000:
        ap.error("budget-minutes 1..10; max-candidates 20..1000")
    project=args.project.expanduser().resolve()
    if args.archive:
        archives=[p.expanduser().resolve() for p in args.archive]
    else:
        archives=sorted((project/"results"/"official_sungrazer_controls").glob("soho*.zip"))
    if not archives:
        print("No training ZIPs: download via tools/inventory_official_comet_sets.py --fetch")
        return
    compatible=[]
    print("=== Independent Sungrazer C3 training inventory ===",flush=True)
    for path in archives:
        try:
            case=identify_c3_training(path)
            print(f"{case['dataset']}: C2 positions={case['positions_c2']}, "
                  f"C3 positions={case['positions_c3']}, "
                  f"matched C3 GIFs={case['matched_c3_gifs']}",flush=True)
            if case["matched_c3_gifs"]>=5:
                compatible.append(case)
        except Exception as e:
            print(f"{path.name}: skipped ({e})",flush=True)
    if not args.execute:
        print("PLAN ONLY. Use --execute to replay compatible C3 training sets.",flush=True)
        if not compatible:
            print("No C3 positive control is ready. Need >=5 labelled C3 GIF frames.")
        return
    if not compatible:
        raise SystemExit("No C3 dataset has >=5 aligned observations; do not test C2 as C3.")
    binary=(args.binary or
        project/"results"/"trajectory_gate_ab"/"comet_hunter_trajectory_experiment").expanduser().resolve()
    if not binary.is_file():
        ap.error("Experimental CUDA binary not found: "+str(binary))
    help_text=subprocess.run([str(binary),"--help"],capture_output=True,
                             text=True,check=True).stdout
    if "--trajectory-null-control" not in help_text:
        ap.error("Test binary does not support the experimental null control")
    if args.include_spatial and "--spatial-balanced-peaks" not in help_text:
        ap.error("Test binary does not support --spatial-balanced-peaks; rebuild the isolated test binary")
    output=(args.output or project/"results"/"sungrazer_c3_truth_replay").expanduser().resolve()
    output.mkdir(parents=True,exist_ok=True)
    report={"training_source":"Sungrazer official training ZIPs",
            "camera":"c3","native_resolution":1024,
            "image_preprocessing":"GIF first-frame to JPEG q2 WITHOUT resize",
            "comparison":"independent labelled positions, upper-left origin",
            "production_unchanged":True,"datasets":[]}
    for case in compatible:
        entry={k:v for k,v in case.items() if k!="selected"}
        entry["runs"]=[]
        report["datasets"].append(entry)
        with tempfile.TemporaryDirectory(prefix="sungrazer_c3_benchmark_",dir="/dev/shm") as tmp:
            stage=Path(tmp)
            try:
                truth=convert_training_gifs(Path(case["archive"]),case["selected"],stage)
            except Exception as e:
                entry["error"]="STAGING_FAILED: "+str(e)
                print(f"{case['dataset']}: {entry['error']}",flush=True)
                continue
            variants=dict(VARIANTS)
            if args.include_spatial:
                variants["spatial_only"]=["--within-window-stationary-filter","--spatial-balanced-peaks"]
                variants["spatial_and_null"]=["--within-window-stationary-filter","--spatial-balanced-peaks","--trajectory-integrity-filter","--trajectory-null-control"]
            for variant, flags in variants.items():
                if busy_cuda():
                    print("Production GPU detector currently busy; stopping A/B replay safely.",flush=True)
                    entry["runs"].append({"variant":variant,"status":"GPU_BUSY"})
                    break
                logfile=output/f"{case['dataset']}_{variant}.log"
                print(f"REPLAY {case['dataset']} {variant}: {len(truth)} real labelled C3 frames",flush=True)
                try:
                    code,tracks,seconds=run_cuda(
                        binary,project,stage,args.budget_minutes,args.max_candidates,flags,logfile)
                except Exception as e:
                    entry["runs"].append({"variant":variant,"status":"ERROR","error":str(e)})
                    break
                if code != 0:
                    entry["runs"].append({
                        "variant":variant,"status":"CUDA_ERROR","exit_code":code,
                        "log":str(logfile)})
                    break
                result=match_comet(tracks,truth)
                result.update({"variant":variant,"status":"OK","candidate_count":len(tracks),
                               "cap_hit":len(tracks)>=args.max_candidates,
                               "runtime_s":round(seconds,1),"log":str(logfile)})
                entry["runs"].append(result)
                print(json.dumps(result,indent=2),flush=True)
        (output/"report.json").write_text(json.dumps(report,indent=2)+"\n")
    print("Report:",output/"report.json")
    print("A filter that misses the real independently labelled C3 comet MUST NOT be deployed.")


if __name__=="__main__":
    main()
