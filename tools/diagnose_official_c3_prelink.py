#!/usr/bin/env python3
"""Diagnose WHY CUDA misses independently labelled SOHO C3 practice comets.

Read-only CPU replay of CUDA's first-stage local-maximum detector using
the SAME staged 1024px GIF->JPEG conversion as benchmark_official_practice_c3.

At each official comet x/y, asks:
1. Is the labelled position inside detector's spatial masks?
2. Is there a local maximum within +/- 3px, and its peak residual?
3. Does its brightness pass per-level threshold?
4. Does it survive the brightest-only maxPoints cap *BEFORE* GPU linking?

If a comet point is already absent before linking, tweaking track filters
cannot restore it. We do not inject known comet coordinates into the hunter.
No GPU use, no network, no production changes.
Numpy and ffmpeg/ffprobe are required for this offline diagnostic.

Usage:
  python3 tools/diagnose_official_c3_prelink.py \
    --project ~/comethunting/comet_hunter_native_cuda_v7_archive \
    --dataset soho1510 --dataset soho732
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess
import tempfile
import zipfile

try:
    import numpy as np
except ImportError as exc:
    raise SystemExit("Numpy required for CPU pre-link replay: import numpy failed") from exc

from benchmark_official_practice_c3 import (
    convert_training_gifs, identify_c3_training
)

# Parameters matched to cuda/comet_hunter_archive.cu in cuda-source-review.
# If they change upstream, change this script AND rerun host checks.
LEVELS = (0, 5, 10, 15, 18)
RADIUS = 3
BORDER = 24
CENTER_MASK_FRACTION = 0.09
BACKGROUND_RADIUS = 4


def jpeg_gray(path: Path):
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x", str(path)
    ], capture_output=True, text=True, check=True, timeout=30)
    w, h = map(int, probe.stdout.strip().splitlines()[0].split("x"))
    if w != 1024 or h != 1024:
        raise ValueError(f"Original practice image is {w}x{h}; refusing resize")
    proc = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(path), "-frames:v", "1",
        "-pix_fmt", "gray", "-f", "rawvideo", "-"
    ], capture_output=True, check=True, timeout=45)
    if len(proc.stdout) != w*h:
        raise ValueError(f"Bad decoded grayscale size for {path.name}")
    return np.frombuffer(proc.stdout, dtype=np.uint8).reshape(h, w)


def raw_maxima(gray, border=BORDER, mask_fraction=CENTER_MASK_FRACTION):
    """Vectorized equivalent of CUDA 8-neighbour peak + 9x9 ring-background.

    CUDA: border max(args.border,args.edgeMask)=24;
    skip center mask radius 0.09*min(width,height);
    a centre >= all 8 immediate neighbours counts as local maximum (ties OK);
    bg mean of [-4,+4]^2 EXCLUDING immediate 3x3 = 72 pixels.
    """
    h, w = gray.shape
    if h < 12 or w < 12:
        raise ValueError("Input image too small")
    image = gray.astype(np.int32, copy=False)
    inner = image[4:-4,4:-4]
    peak = np.ones(inner.shape, dtype=bool)
    for dy in (-1,0,1):
        for dx in (-1,0,1):
            if dy or dx:
                peak &= inner >= image[4+dy:h-4+dy,4+dx:w-4+dx]

    integral = np.pad(image.cumsum(axis=0,dtype=np.int64)
                          .cumsum(axis=1,dtype=np.int64),
                      ((1,0),(1,0)))
    s9 = (integral[9:,9:]-integral[:-9,9:]
          -integral[9:,:-9]+integral[:-9,:-9])
    s3 = (integral[6:-3,6:-3]-integral[3:-6,6:-3]
          -integral[6:-3,3:-6]+integral[3:-6,3:-6])
    residual = inner - (s9-s3)/72.0

    yy, xx = np.indices(inner.shape)
    xx = xx + 4
    yy = yy + 4
    # The CUDA mask uses strict less than radius^2, not less or equal.
    center_x, center_y=(w-1)*0.5,(h-1)*0.5
    center_radius=min(w,h)*mask_fraction
    peak &= ((xx>=border)&(yy>=border)
             &(xx<w-border)&(yy<h-border))
    peak &= ((xx-center_x)**2+(yy-center_y)**2 >= center_radius**2)
    indices=np.where(peak)
    return (xx[indices].astype(np.int32),
            yy[indices].astype(np.int32),
            residual[indices].astype(np.float32))


def truth_rank(maxima, truth_xy, level, radius=RADIUS):
    """Top point rank, with deterministic CUDA tie-break strength/y/x."""
    xs, ys, residual = maxima
    threshold=max(5.0,18.0-level*0.75)
    max_points=min(6000,700+level*300)
    dx=xs-float(truth_xy[0])
    dy=ys-float(truth_xy[1])
    matches=(dx*dx+dy*dy <= radius*radius)
    if not np.any(matches):
        return {"reason":"NO_LOCAL_MAXIMUM_WITHIN_3PX", "nearest_peak_distance_px":
                round(float(np.hypot(dx,dy).min()),2) if len(xs) else None,
                "threshold":threshold, "point_cap":max_points, "in_cuda_points":False}
    best_residual=float(residual[matches].max())
    accepted = residual >= threshold
    if not np.any(matches & accepted):
        return {"reason":"BELOW_RESIDUAL_THRESHOLD",
                "best_residual":round(best_residual,2),
                "threshold":threshold,"point_cap":max_points,"in_cuda_points":False}
    # Number of brighter or tied earlier entries (the actual stable sort key).
    order=np.lexsort((xs[accepted],ys[accepted],-residual[accepted]))
    raw_positions=np.nonzero(accepted)[0][order]
    match_indices=np.where(matches)[0]
    matching_ranks=np.flatnonzero(np.isin(raw_positions,match_indices))
    if len(matching_ranks)==0:
        raise AssertionError("Comet peak above threshold but missing from sort")
    rank=int(matching_ranks.min()+1)
    return {"reason":"IN_CUDA_POINT_LIST" if rank<=max_points else "OUTSIDE_BRIGHTNESS_CAP",
            "peak_rank":rank,"best_residual":round(best_residual,2),
            "threshold":threshold,"point_cap":max_points,
            "available_maxima":int(accepted.sum()),
            "in_cuda_points": bool(rank<=max_points)}


def sample_mask_reason(x,y, w=1024,h=1024):
    if x<BORDER or y<BORDER or x>=w-BORDER or y>=h-BORDER:
        return "MASKED_IMAGE_BORDER"
    center_x,center_y=(w-1)*0.5,(h-1)*0.5
    if (x-center_x)**2+(y-center_y)**2 < \
            (min(w,h)*CENTER_MASK_FRACTION)**2:
        return "MASKED_SOLAR_CENTRE"
    return None


def summarize(result):
    records=result["frames"]
    for level in LEVELS:
        key=str(level)
        counts={}
        for row in records:
            outcome=row["levels"][key]["reason"]
            counts[outcome]=counts.get(outcome,0)+1
        result["level_summary"][key]={
            "threshold":max(5.,18.-level*0.75),
            "point_cap":min(6000,700+level*300),
            "comet_visible_to_linker":counts.get("IN_CUDA_POINT_LIST",0),
            "labelled_frames":len(records),
            "reasons":counts,
        }
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project",type=Path,required=True)
    parser.add_argument("--dataset",action="append",
                        help="One official practice ZIP name (e.g. soho1510), repeatable")
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    project=args.project.expanduser().resolve()
    names=args.dataset or ["soho732","soho1264","soho1510"]
    root=project/"results"/"official_sungrazer_controls"
    output=(args.output or project/"results"/"official_prelink_diagnostics").expanduser().resolve()
    output.mkdir(parents=True,exist_ok=True)

    for name in names:
        path=root/(name+".zip")
        if not path.is_file():
            print(f"{name}: zip missing at {path}; no network requests made",flush=True)
            continue
        case=identify_c3_training(path)
        if case["matched_c3_gifs"]<5:
            print(f"{name}: only {case['matched_c3_gifs']} C3-labelled frames; skip",flush=True)
            continue
        report={"dataset":name, "camera":"c3", "resolution":1024,
                "staging":"official GIF to JPEG q2, no resizing; ffmpeg gray decode approximation",
                "note":"CPU feature-stage estimate, not identical to NVJPEG due decoder rounding",
                "frames":[],"level_summary":{}}
        with tempfile.TemporaryDirectory(prefix="soho_prelink_",dir="/dev/shm") as temp:
            stage=Path(temp)
            truth=convert_training_gifs(path,case["selected"],stage)
            for i,p in enumerate(truth,1):
                gray=jpeg_gray(stage/p["staged_jpeg"])
                x,y=p["x"],p["y"]
                masked=sample_mask_reason(x,y)
                maxima=raw_maxima(gray)
                levels={}
                for level in LEVELS:
                    levels[str(level)]=(
                        {"reason":masked,"in_cuda_points":False}
                        if masked else truth_rank(maxima,(x,y),level)
                    )
                row={
                    "time":str(p["time"]),"x":x,"y":y,
                    "image":p["staged_jpeg"],"levels":levels,
                }
                report["frames"].append(row)
                print(f"{name} image {i}/{len(truth)} "
                      f"coordinate=({x:g},{y:g}), "
                      f"level0={levels['0']['reason']}, "
                      f"level18={levels['18']['reason']}",flush=True)
        summarize(report)
        dest=output/f"{name}.json"
        dest.write_text(json.dumps(report,indent=2)+"\n")
        print(f"\n=== {name} independent comet visibility before CUDA linker ===")
        for level in LEVELS:
            summary=report["level_summary"][str(level)]
            print(f"Level {level}, threshold {summary['threshold']:.2f}, "
                  f"strongest {summary['point_cap']}: "
                  f"{summary['comet_visible_to_linker']}/{summary['labelled_frames']} "
                  f"comet positions available; reasons={summary['reasons']}")
        print("Report:",dest,flush=True)
    print("Interpretation: if few comet points survive, fix local feature extraction "
          "or point selection. If most survive but no trajectory is returned, "
          "investigate seed search/track linking. Neither outcome proves comet "
          "recall on unmodified original archive JPEGs.")


if __name__=="__main__":
    main()
