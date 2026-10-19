#!/usr/bin/env python3
"""Offline CUDA known-comet replay: no network traffic, no live-hunter writes.

The five controls come from the project's published known-object review records.
Temporal overlap alone IS NOT a validated recovery. Without independently
recorded x/y ground truth, positional recall is reported as unknown.

Usage on Bourbaki:
  python3 tools/benchmark_known_comets.py --project \
    ~/comethunting/comet_hunter_native_cuda_v7_archive --execute

Pass --frames-root repeatedly to include additional ORIGINAL 512/1024 JPEG
archive folders. The script never fabricates higher resolution by upsampling.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import defaultdict
from pathlib import Path

UTC = dt.timezone.utc
IMAGE = re.compile(r"^(20\d{6}_\d{4})_c3_(512|1024)\.jpe?g$", re.I)
CANDIDATE = re.compile(
    r"^(C\d+)\s+\[(HIGH|MEDIUM|LOW)\].*?\bframes=(\d+).*?"
    r"\bRMS=([0-9.]+).*?\bscore=([0-9.]+)", re.I
)
POINT = re.compile(
    r"(20\d{6}_\d{4}_c3_(?:512|1024)\.jpe?g)\s+"
    r"\((-?[0-9.]+),\s*(-?[0-9.]+)\)", re.I
)


def when(value):
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    value = str(value)
    if IMAGE.match(value):
        return dt.datetime.strptime(value[:13], "%Y%m%d_%H%M").replace(tzinfo=UTC)
    # SOHO and dashboard event timestamps represent UTC even when their ISO
    # strings omit an explicit offset. datetime.astimezone() assumes the
    # machine's LOCAL timezone for naive values (e.g. Sydney => -11h).
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def controls(dashboard):
    records = json.loads(Path(dashboard).read_text(encoding="utf-8"))["candidates"]
    result = []
    for row in records:
        if row.get("known_object") and row.get("first_seen") and row.get("last_seen"):
            result.append({
                "event_id": row["event_id"],
                "known_object": row["known_object"],
                "first": when(row["first_seen"]), "last": when(row["last_seen"]),
                "historical_frames": int(row.get("frames") or 0),
                "historical_review_class": row.get("review_class"),
            })
    if not result:
        raise ValueError("No known-object controls found; cannot benchmark recall.")
    return result


def image_roots(project, explicit_roots):
    if explicit_roots:
        return [Path(p).expanduser().resolve() for p in explicit_roots]
    # Historical processing may have used sibling archive/work directories
    # or nonstandard caches. Scan the whole dedicated comet workspace rather
    # than guessing five directory names inside one project.
    roots = [project.parent]
    shm = Path("/dev/shm")
    if shm.is_dir():
        roots += [p for p in shm.iterdir()
                  if p.is_dir() and ("comet" in p.name.lower() or "soho" in p.name.lower())]
    return [p for p in roots if p.is_dir()]


def index_images(roots, max_entries=300000):
    by_size = {512: defaultdict(list), 1024: defaultdict(list)}
    inspected = 0
    archived_inputs = []
    root_counts = {}
    for root in roots:
        if not root.exists():
            continue
        root_matches = {512: 0, 1024: 0}
        # Do not interpret generated video frames or cropped reviews as original SOHO input.
        for current, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in
                       {".git", "review", ".frames", "media", "__pycache__",
                        "zoom_raw", "zoom_annotated", "full_frame", "node_modules",
                        "known_comet_replay"}]
            for name in files:
                inspected += 1
                if inspected > max_entries:
                    raise RuntimeError(
                        f"Image discovery exceeded --max-entries={max_entries}; "
                        "supply one or more --frames-root options pointing at original JPEG folders."
                    )
                match = IMAGE.match(name)
                if match:
                    date = when(name)
                    size = int(match.group(2))
                    by_size[size][date].append(Path(current) / name)
                    root_matches[size] += 1
                elif len(archived_inputs) < 30 and name.lower().endswith(
                        (".tar", ".tar.gz", ".tgz", ".zip", ".tar.zst", ".fits", ".fits.gz")):
                    archived_inputs.append(str(Path(current)/name))
        root_counts[str(root)] = root_matches
    return by_size, inspected, root_counts, archived_inputs


def pick_frames(index, start, end, pad_hours=1.0):
    lo = start - dt.timedelta(hours=pad_hours)
    hi = end + dt.timedelta(hours=pad_hours)
    entries = []
    for stamp in sorted(index):
        if lo <= stamp <= hi:
            # Same timestamp can occur in multiple archive caches.
            # One original frame per timestamp, never duplicate it.
            choices = index[stamp]
            chosen = next((p for p in choices if "completed" in str(p).lower()),
                          choices[0])
            entries.append(chosen)
    return entries


def verify_dimensions(image, size):
    probe = shutil.which("ffprobe")
    if not probe:
        return "ffprobe_missing"
    try:
        result = subprocess.run([
            probe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", str(image)
        ], text=True, capture_output=True, timeout=20)
        observed = result.stdout.strip().replace("\n", "")
        return "ok" if result.returncode == 0 and observed == f"{size},{size}" \
            else f"size_mismatch:{observed or result.stderr.strip()[:100]}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"probe_error:{exc}"


def parse_output(stdout):
    rows = []
    lines = stdout.splitlines()
    for i, line in enumerate(lines):
        head = CANDIDATE.search(line.strip())
        if not head:
            continue
        first = last = None
        for line2 in lines[i+1:i+6]:
            if CANDIDATE.search(line2.strip()):
                break  # Never steal endpoints from the next CUDA candidate.
            m = POINT.search(line2)
            if m:
                point = {"time": when(m.group(1)),
                         "x": float(m.group(2)), "y": float(m.group(3))}
                # Native CUDA prints "last :" (with a space before colon).
                # Older and synthetic logs may use "last:" or "first:".
                if re.search(r"\bfirst\s*:",line2,re.I):
                    first = point
                elif re.search(r"\blast\s*:",line2,re.I):
                    last = point
        if first and last:
            if first["time"] > last["time"]:
                first, last = last, first
            rows.append({"id": head.group(1), "priority": head.group(2).upper(),
                         "frames": int(head.group(3)), "rms": float(head.group(4)),
                         "score": float(head.group(5)), "first": first, "last": last})
    return rows


def truth_rows(truth_file, event_id):
    """Optional independent ground-truth file: event_id,utc,x1024,y1024."""
    if not truth_file:
        return []
    result = []
    with Path(truth_file).open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("event_id") == event_id:
                result.append((when(row["utc"]), float(row["x1024"]),
                               float(row["y1024"])))
    return result


def positional_errors(candidate, truth, source_size):
    errors = []
    left, right = candidate["first"], candidate["last"]
    total = (right["time"] - left["time"]).total_seconds()
    if total <= 0:
        return []
    for t, x, y in truth:
        if t < left["time"] or t > right["time"]:
            continue
        u = (t - left["time"]).total_seconds() / total
        xp = (left["x"] + (right["x"]-left["x"])*u) * 1024/source_size
        yp = (left["y"] + (right["y"]-left["y"])*u) * 1024/source_size
        errors.append(((xp-x)**2+(yp-y)**2)**0.5)
    return sorted(errors)


def evaluate(parsed, event, size, truth=None):
    truth = truth or []
    a, b = event["first"], event["last"]
    temporal = [r for r in parsed if
                min(r["last"]["time"],b) >= max(r["first"]["time"],a)
                and r["frames"] >= 5]
    matches = []
    for r in temporal:
        errors = positional_errors(r, truth, size)
        if len(errors) >= 3 and errors[len(errors)//2] <= 20:
            matches.append((errors[len(errors)//2],r["id"]))
    matches.sort()
    return {
        "raw_tracks": len(parsed),
        "temporal_hypotheses": len(temporal),
        "independent_truth_points": len(truth),
        "positional_recovered": bool(matches) if len(truth)>=3 else None,
        "best_median_error_1024_px": round(matches[0][0],2) if matches else None,
        "matched_candidates": [x[1] for x in matches[:5]],
        "non_matching_temporal_hypotheses": max(0,len(temporal)-len(matches))
            if len(truth)>=3 else None,
        "interpretation": ("positional_match" if matches else
            "no_positional_match" if len(truth)>=3 else
            "temporal_only_unverified"),
    }


def busy_cuda():
    me = os.getpid()
    for p in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            if int(p.parent.name) == me:
                continue
            args = [os.fsdecode(v) for v in p.read_bytes().split(b"\0") if v]
            if args and "comet_hunter_archive" in Path(args[0]).name:
                return True
        except (ValueError, OSError):
            continue
    return False


def run_cuda(binary, project, images, resolution, minutes, max_candidates, outlog):
    # No SOHO downloads and no modifications to the realtime input cache.
    # Use existing JPEGs through hardlinks or, across devices, symlinks.
    with tempfile.TemporaryDirectory(prefix="soho_comet_replay_",
                                     dir="/dev/shm" if Path("/dev/shm").is_dir()
                                     else None) as temp:
        staging = Path(temp)
        for src in images:
            dest = staging / src.name
            try:
                os.link(src, dest)
            except OSError:
                dest.symlink_to(src.resolve())
        args = [str(binary), "--camera", "c3", "--resolution", str(resolution),
                "--input-dir", str(staging), "--budget-minutes", str(minutes),
                "--min-frames", "5", "--max-candidates", str(max_candidates)]
        try:
            proc = subprocess.run(args, cwd=project, text=True, capture_output=True,
                                  timeout=max(300, minutes*120))
            stdout = proc.stdout + "\n" + proc.stderr
            outlog.write_text(stdout, encoding="utf-8")
            return proc.returncode, parse_output(stdout), None
        except subprocess.TimeoutExpired as exc:
            outlog.write_text(str(exc), encoding="utf-8")
            return None, [], "CUDA_TIMEOUT"


def save_report(folder, report):
    folder.mkdir(parents=True,exist_ok=True)
    (folder/"report.json").write_text(json.dumps(report, indent=2)+"\n",encoding="utf-8")
    lines = ["# SOHO known-comet offline replay", "",
        "The replay never contacts SOHO or alters the realtime cache.",
        "**Time-overlap candidates are not validated known-comet recoveries.**",
        "Positional recall is unknown unless independent reference coordinates were supplied.",
        "", "| Control | Resolution | Frames | Status | Temporal tracks | Positional recovery |",
        "|---|---:|---:|---|---:|---|"]
    for r in report["runs"]:
        label=r["control"]
        rec=r.get("positional_recovered")
        rec="UNKNOWN" if rec is None else "YES" if rec else "NO"
        lines.append(f"| {label} | {r['resolution']} | {r['source_frames']} | "
                     f"{r['status']} | {r.get('temporal_hypotheses','—')} | {rec} |")
    lines += ["", "Inspect report.json and detector logs before changing CUDA thresholds.",
              "An empty/missing original image folder is an INPUT GAP, not a failure to detect a comet.",
              "If original 1024 JPEGs are missing, recover them through the existing rate-limited SOHO archive pipeline."]
    (folder/"report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, type=Path)
    ap.add_argument("--frames-root", type=Path, action="append", default=[])
    ap.add_argument("--dashboard", type=Path, default=Path(__file__).resolve().parents[1] /
                    "docs/data/candidates.json")
    ap.add_argument("--truth", type=Path, help="CSV: event_id,utc,x1024,y1024")
    ap.add_argument("--execute", action="store_true",help="Run CUDA; default discovery-only")
    ap.add_argument("--minutes",type=int,default=5)
    ap.add_argument("--max-candidates",type=int,default=500)
    ap.add_argument("--max-entries",type=int,default=300000)
    ap.add_argument("--wait-for-gpu-seconds",type=int,default=900)
    ap.add_argument("--report-dir",type=Path)
    args=ap.parse_args()
    project=args.project.expanduser().resolve()
    binary=project/"comet_hunter_archive"
    results=args.report_dir or project/"results"/"known_comet_replay"/dt.datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    cases=controls(args.dashboard)
    roots=image_roots(project,args.frames_root)
    indexed,inspected,root_counts,archive_hints=index_images(roots,args.max_entries)
    report={"generated_at":dt.datetime.now(UTC).isoformat(),
            "execution_requested":args.execute,
            "project":str(project),"searched_roots":[str(p) for p in roots],
            "inspected_file_entries":inspected,
            "image_counts_by_root":root_counts,
            "possible_archived_input_files":archive_hints,
            "original_image_counts":{str(k):sum(map(len,v.values())) for k,v in indexed.items()},
            "controls":len(cases),"runs":[]}
    print(f"Controls: {len(cases)} | files inspected: {inspected} | roots: {roots}",flush=True)
    for root,counts in root_counts.items():
        print(f"Input inventory: {root} => {counts}",flush=True)
    if archive_hints:
        print("Possible compressed/FITS inputs (not directly suitable for CUDA replay):",flush=True)
        for candidate in archive_hints[:15]:
            print("  "+candidate,flush=True)
    if args.execute and not binary.is_file():
        raise SystemExit(f"CUDA binary missing: {binary}")
    for event in cases:
        for resolution in (512,1024):
            frames=pick_frames(indexed[resolution],event["first"],event["last"])
            row={"control":event["known_object"],"event_id":event["event_id"],
                 "resolution":resolution,"source_frames":len(frames),
                 "status":"NO_ORIGINAL_IMAGES" if len(frames)<5 else "READY",
                 "historical_frames":event["historical_frames"]}
            report["runs"].append(row)
            if len(frames)>=5:
                quality=verify_dimensions(frames[0],resolution)
                if quality!="ok":
                    row["status"]="INVALID_INPUT_DIMENSIONS"
                    row["image_diagnostic"]=quality
            if row["status"]=="READY" and args.execute:
                deadline=time.monotonic()+args.wait_for_gpu_seconds
                while busy_cuda() and time.monotonic()<deadline:
                    time.sleep(10)
                if busy_cuda():
                    row["status"]="GPU_BUSY"
                else:
                    logfile=results/f"{event['event_id']}_{resolution}.log"
                    logfile.parent.mkdir(parents=True,exist_ok=True)
                    code,parsed,error=run_cuda(binary,project,frames,resolution,
                                                args.minutes,args.max_candidates,logfile)
                    row["cuda_returncode"]=code
                    row["log_file"]=logfile.name
                    row["status"]="CUDA_ERROR" if code != 0 else "COMPLETE"
                    if error:
                        row["error"]=error
                    if code==0:
                        row.update(evaluate(parsed,event,resolution,
                                            truth_rows(args.truth,event["event_id"])))
            print(f"{event['known_object']} {resolution}px: {len(frames)} input | "
                  f"{row['status']} | temporal={row.get('temporal_hypotheses','—')} | "
                  f"positional={row.get('positional_recovered','UNKNOWN')}",flush=True)
            save_report(results,report)
    if not any(r["status"]=="COMPLETE" for r in report["runs"]):
        print("No completed CUDA replay. This is an image-data availability result, "
              "NOT an assessment of detector recall.",flush=True)
    print(f"Report: {results/'report.md'}",flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
