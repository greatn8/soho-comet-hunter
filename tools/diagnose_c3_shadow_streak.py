#!/usr/bin/env python3
"""Test whether persistent dark LASCO C3 structure attracts false trajectories.

OFFLINE, READ ONLY: uses existing 512px JPEG cache and the last complete
12/48/336h native CUDA logs. It does not fetch SOHO images, use the GPU,
modify the live detector, mark comet discoveries, or mask any image area.

A dark streak may yield positive residual peaks along its bright EDGES.
Estimate shadows/edges in a time-median image, then compare the incidence
of reconstructed track paths on those pixels with spatially translated
control paths in the same quadrant. Report measurements, not a verdict.

Usage:
  python3 tools/diagnose_c3_shadow_streak.py \
    --project ~/comethunting/comet_hunter_native_cuda_v7_archive

Dependencies: numpy, ffmpeg. This is a grayscale/median-image approximation
of the NVJPEG CUDA pixel stream. Its association is observational and is not
proof that the dark feature *causes* the detected paths.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

try:
    import numpy as np
except ImportError as exc:
    raise SystemExit("Requires numpy (offline CPU-only diagnostic)") from exc

from realtime_hunter import parse_candidates, candidate_quadrant

SIZE = 512
WINDOWS = (12, 48, 336)
LOG_MATCH = re.compile(r"detector_(\d{8}T\d{6}Z)_336h\.log$")


def latest_complete_logs(results: Path):
    """Select three logs from the SAME CUDA cycle, never mix run timestamps."""
    for file in sorted(results.glob("detector_*_336h.log"), reverse=True):
        match = LOG_MATCH.fullmatch(file.name)
        if not match:
            continue
        runid = match.group(1)
        paths = {hours: results / f"detector_{runid}_{hours}h.log"
                 for hours in WINDOWS}
        if all(path.is_file() for path in paths.values()):
            return runid, paths
    raise FileNotFoundError("No complete 12/48/336h detector log trio found")


def load_gray(path: Path):
    process = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
        "-pix_fmt", "gray", "pipe:1"
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40)
    if process.returncode or len(process.stdout) != SIZE*SIZE:
        raise RuntimeError(f"Cannot decode {path.name} as {SIZE}x{SIZE} grayscale: "
                           +process.stderr.decode(errors="replace")[-200:])
    return np.frombuffer(process.stdout,dtype=np.uint8).reshape(SIZE,SIZE).copy()


def choose_evenly(items, count):
    if not items:
        return []
    count=min(len(items),count)
    indexes=np.linspace(0,len(items)-1,count)
    return [items[i] for i in sorted(set(int(round(x)) for x in indexes))]


def shadow_masks(gray_stack, dark_threshold=10., edge_threshold=12.,
                 horizontal_offset=16):
    """Persistent dark deficits and their bright edges (independent of tracks).

    Use a per-pixel temporal median to suppress transients, then compare each
    pixel against a symmetric horizontal-background estimate. Symmetry removes
    a locally linear coronal brightness gradient. The edge mask is restricted
    to within 6 pixels of a dark deficit: ordinary stars are not dark streaks.
    """
    data=np.asarray(gray_stack)
    if data.ndim != 3 or data.shape[1:] != (SIZE,SIZE):
        raise ValueError("Require at least one 512x512 grayscale image")
    if not 6<=horizontal_offset<=48:
        raise ValueError("horizontal_offset must be 6..48")
    med=np.median(data,axis=0).astype(np.float32)
    d=horizontal_offset
    dark=np.zeros_like(med)
    dark[:,d:-d] = (med[:,:-2*d]+med[:,2*d:])*0.5-med[:,d:-d]
    dark_pixels=dark>=dark_threshold

    edge=np.zeros_like(med)
    edge[:,4:-4]=np.abs(med[:,8:]-med[:,:-8])
    near_shadow=np.zeros_like(dark_pixels)
    for offset in range(-6,7):
        start=max(0,offset)
        end=min(SIZE,SIZE+offset)
        near_shadow[:,start:end] |= dark_pixels[:,start-offset:end-offset]
    dark_edge=(edge>=edge_threshold) & near_shadow

    # Match the CUDA local-peak image field, excluding border and solar mask.
    yy,xx=np.indices((SIZE,SIZE))
    field=(xx>=24)&(yy>=24)&(xx<SIZE-24)&(yy<SIZE-24)
    field &= (xx-255.5)**2+(yy-255.5)**2 >= (0.09*SIZE)**2
    return dark_pixels & field, dark_edge & field, med


def track_path_fraction(track, feature, xshift=0, steps=13, quadrant=None):
    """Fraction of interpolated path samples touching a persistent feature."""
    first,last=track["first"],track["last"]
    xs=np.linspace(first["x"],last["x"],steps)+xshift
    ys=np.linspace(first["y"],last["y"],steps)
    xx=np.rint(xs).astype(int)
    yy=np.rint(ys).astype(int)
    valid=(xx>=24)&(xx<488)&(yy>=24)&(yy<488)
    if quadrant=="LOWER_LEFT":
        valid &= (xx<256)&(yy>=256)
    if quadrant=="UPPER_RIGHT":
        valid &= (xx>=256)&(yy<256)
    if not np.any(valid):
        return None
    return float(feature[yy[valid],xx[valid]].mean())


def contact_summary(tracks, feature, quadrant, offsets=(-48,-24,24,48)):
    measured=[]
    for track in tracks:
        if candidate_quadrant(track)!=quadrant:
            continue
        observed=track_path_fraction(track,feature,quadrant=quadrant)
        controls=[track_path_fraction(track,feature,xshift=s,quadrant=quadrant)
                  for s in offsets]
        controls=[c for c in controls if c is not None]
        if observed is None or len(controls)<2:
            continue
        measured.append((observed,float(np.mean(controls))))
    if not measured:
        return {"paths_compared":0,"path_contact_pct":None,
                "offset_control_contact_pct":None,"contact_excess_percentage_points":None}
    observation=float(np.mean([r[0] for r in measured]))
    null=float(np.mean([r[1] for r in measured]))
    return {
        "paths_compared":len(measured),
        "path_contact_pct":round(observation*100,2),
        "offset_control_contact_pct":round(null*100,2),
        "contact_excess_percentage_points":round((observation-null)*100,2),
    }


def top_columns(feature, quadrant, n=8):
    yslice=slice(264,488) if quadrant=="LOWER_LEFT" else slice(24,248)
    xs=range(24,248) if quadrant=="LOWER_LEFT" else range(264,488)
    strength=feature[yslice,:].mean(axis=0)
    chosen=sorted(xs,key=lambda x:(-strength[x],x))
    return [{"x":int(x),"feature_coverage_pct":round(float(strength[x])*100,2)}
            for x in chosen[:n]]


def quadrant_counts(tracks):
    counts={q:0 for q in
            ("UPPER_LEFT","UPPER_RIGHT","LOWER_LEFT","LOWER_RIGHT","UNKNOWN")}
    for t in tracks:
        q=candidate_quadrant(t)
        counts[q]=counts.get(q,0)+1
    return counts


def main():
    p=argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project",type=Path,required=True)
    p.add_argument("--cache",type=Path,
        help="Defaults to existing /dev/shm/comet_realtime_$USER_c3_512")
    p.add_argument("--samples",type=int,default=15)
    p.add_argument("--dark-threshold",type=float,default=10.)
    p.add_argument("--edge-threshold",type=float,default=12.)
    p.add_argument("--horizontal-offset",type=int,default=16)
    p.add_argument("--output",type=Path)
    args=p.parse_args()
    if not 5<=args.samples<=40:
        p.error("--samples must be between 5 and 40")
    project=args.project.expanduser().resolve()
    root=project/"results"/"realtime"
    runid,logs=latest_complete_logs(root)
    tracks=[]
    counts={}
    for hours,path in logs.items():
        group=parse_candidates(path.read_text(encoding="utf-8",errors="replace"))
        tracks.extend(group)
        counts[str(hours)]=len(group)
    cache=(args.cache or Path("/dev/shm")/
           f"comet_realtime_{os.environ.get('USER','user')}_c3_512").expanduser()
    if not cache.is_dir():
        raise SystemExit(f"Missing live cache {cache}. Nothing downloaded.")
    # Sample the full retained temporal range, not only the latest few frames.
    files=sorted(cache.glob("*_c3_512.jpg"))
    selected=choose_evenly(files,args.samples)
    if len(selected)<5:
        raise SystemExit(f"Too few cached frames ({len(selected)}); no downloads made.")
    print(f"Offline shadow audit for {runid}; {len(tracks)} detector tracks; "
          f"{len(selected)} cached image samples (from {len(files)} cached).",flush=True)
    grays=[]
    for i,path in enumerate(selected,1):
        grays.append(load_gray(path))
        print(f"  decoded {i}/{len(selected)} {path.name}",flush=True)
    shadow,edge,_=shadow_masks(np.stack(grays),dark_threshold=args.dark_threshold,
        edge_threshold=args.edge_threshold,horizontal_offset=args.horizontal_offset)
    joint=shadow|edge
    sectors=quadrant_counts(tracks)
    artifact_summary={}
    for quadrant in ("LOWER_LEFT","UPPER_RIGHT","UPPER_LEFT","LOWER_RIGHT"):
        result=contact_summary(tracks,joint,quadrant)
        result["tracks_in_quadrant"]=sectors.get(quadrant,0)
        if quadrant in ("LOWER_LEFT","UPPER_RIGHT"):
            result["shadow_columns"]=top_columns(shadow,quadrant)
            result["shadow_or_edge_columns"]=top_columns(joint,quadrant)
        artifact_summary[quadrant]=result
    result={
        "run_id":runid,
        "source":"original cached 512 JPEGs (no SOHO requests)",
        "source_resolution":SIZE,
        "sampled_frame_count":len(selected),
        "sampled_frames":[f.name for f in selected],
        "CUDA_windows":counts,
        "raw_tracks":len(tracks),
        "quadrants":sectors,
        "method":"time-median image; symmetric horizontal shadow deficit and nearby edge; original vs shifted paths",
        "dark_threshold_gray_levels":args.dark_threshold,
        "edge_threshold_gray_levels":args.edge_threshold,
        "horizontal_offset_px":args.horizontal_offset,
        "interpretation_warning":"Association is not causation. Only real image checks and held-out comet controls can validate an artifact rejection rule.",
        "results":artifact_summary,
    }
    outfile=(args.output or root/"shadow_streak_diagnostic.json").expanduser().resolve()
    outfile.parent.mkdir(parents=True,exist_ok=True)
    outfile.write_text(json.dumps(result,indent=2)+"\n")
    print("\n=== RAW TRACKS PER QUADRANT ===")
    for q,num in sectors.items():print(f"{q}: {num}")
    print("\n=== PERSISTENT DARK STRUCTURE/EDGE vs SHIFTED CONTROL PATHS ===")
    for q,row in artifact_summary.items():
        print(f"{q}: {row['paths_compared']} usable tracks, "
              f"observed contact={row['path_contact_pct']}%, "
              f"offset controls={row['offset_control_contact_pct']}%, "
              f"excess={row['contact_excess_percentage_points']} percentage points")
        for col in row.get("shadow_columns",[])[:5]:
            print(f"   shadow column x={col['x']} "
                  f"coverage={col['feature_coverage_pct']}%")
    print("Saved:",outfile)
    print("No source images, CUDA binaries, caches, or live hunter settings modified.")


if __name__=="__main__":
    main()
