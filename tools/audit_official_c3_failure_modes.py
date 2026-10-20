#!/usr/bin/env python3
"""Read-only offline diagnosis of why real C3 512px comet points are lost.

Uses existing independently labelled SOHO practice ZIPs and identical 512px
JPEG staging as benchmark_official_c3_512.py. Separates threshold failure
from selection failure, compares equal-stage translated controls, and reports
local contrast. CPU/ffmpeg approximate CUDA; no GPU, network or cache changes.

python3 tools/audit_official_c3_failure_modes.py --project \
  ~/comethunting/comet_hunter_native_cuda_v7_archive
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import numpy as np

from benchmark_official_practice_c3 import identify_c3_training
from benchmark_official_c3_512 import CONTROL_NAMES, stage_512, truth_at_512
from diagnose_c3_shadow_streak import load_gray
from diagnose_c3_peak_vs_track import (
    cuda_first_pass_peaks, spatial_balanced_indices, moving_box_sum
)
from audit_official_c3_512_points import in_cuda_field

THRESHOLDS = (5.0, 9.75, 18.0)
CAPS = (700, 1500, 3000, 6000)
OFFSETS = ((-28, 0), (28, 0), (0, -28), (0, 28))
RADIUS = 3.0


def contrast_measure(image, x, y):
    """Local background dispersion independent of peak selection."""
    xi, yi = int(round(x)), int(round(y))
    if not (10 <= xi < 502 and 10 <= yi < 502):
        return None
    patch = image[yi-9:yi+10, xi-9:xi+10].astype(np.float32)
    yy, xx = np.indices(patch.shape)
    background = patch[(xx-9)**2 + (yy-9)**2 >= 25]
    centre = patch[7:12, 7:12]
    base = float(np.median(background))
    mad = float(np.median(np.abs(background-base))) * 1.4826
    return {
        "local_peak_above_median": round(float(centre.max())-base, 3),
        "local_mad": round(mad, 3),
        "local_peak_over_mad": round((float(centre.max())-base)/max(1.0, mad), 3),
    }


def nearest(xs, ys, x, y):
    if not len(xs):
        return {"hit": False, "rank": None, "distance": None}
    dist = np.hypot(xs.astype(float)-x, ys.astype(float)-y)
    eligible = np.flatnonzero(dist <= RADIUS)
    if not len(eligible):
        return {"hit": False, "rank": None, "distance": round(float(dist.min()), 3)}
    # Peak arrays sorted strongest first.
    rank = int(eligible[0])
    return {"hit": True, "rank": rank+1,
            "distance": round(float(dist[rank]), 3)}


def observe(xs, ys, x, y, selections):
    result = {"raw": nearest(xs, ys, x, y)}
    for name, indices in selections.items():
        result[name] = nearest(xs[indices], ys[indices], x, y)
    return result


def report_stage(records, stage):
    eligible = [r for r in records if r["in_field"]]
    positive = sum(r["truth"][stage]["hit"] for r in eligible)
    controls = [c[stage]["hit"] for r in eligible for c in r["controls"]]
    return {
        "truth_hits": positive, "truth_total": len(eligible),
        "truth_pct": round(100*positive/len(eligible), 2) if eligible else None,
        "control_hits": sum(controls), "control_total": len(controls),
        "control_pct": round(100*sum(controls)/len(controls), 2)
                       if controls else None,
    }


def analyze(images, truth):
    thresholds = {}
    for threshold in THRESHOLDS:
        records = []
        for image, pos in zip(images, truth):
            xs, ys, strength = cuda_first_pass_peaks(image, threshold=threshold)
            selections = {}
            for cap in CAPS:
                selections[f"global_{cap}"] = np.arange(min(len(xs), cap))
                selections[f"spatial_{cap}"] = spatial_balanced_indices(
                    xs, ys, maximum=cap)
            x, y = pos["x"], pos["y"]
            truth_hit = observe(xs, ys, x, y, selections)
            controls = [
                observe(xs, ys, x+dx, y+dy, selections)
                for dx, dy in OFFSETS if in_cuda_field(x+dx, y+dy)
            ]
            records.append({
                "time": pos["time"].isoformat(),
                "x": x, "y": y, "in_field": in_cuda_field(x, y),
                "raw_peaks": len(xs),
                "truth": truth_hit, "controls": controls,
                "contrast": contrast_measure(image, x, y),
            })
        stages = ["raw"] + [
            f"{kind}_{cap}" for cap in CAPS for kind in ("global", "spatial")
        ]
        metrics = {s: report_stage(records, s) for s in stages}
        failures = {
            "threshold_missing": sum(
                r["in_field"] and not r["truth"]["raw"]["hit"] for r in records),
            "lost_by_spatial_700": sum(
                r["in_field"] and r["truth"]["raw"]["hit"] and
                not r["truth"]["spatial_700"]["hit"] for r in records),
            "recovered_if_spatial_3000": sum(
                r["in_field"] and not r["truth"]["spatial_700"]["hit"] and
                r["truth"]["spatial_3000"]["hit"] for r in records),
        }
        thresholds[str(threshold)] = {
            "metrics": metrics, "failure_counts": failures, "frames": records
        }
    return thresholds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--case", action="append", choices=CONTROL_NAMES)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    project = args.project.expanduser().resolve()
    source = project/"results"/"official_sungrazer_controls"
    output = {"study": "512px independent-label threshold vs selection diagnosis",
              "caveat": "CPU/ffmpeg approximation; spatial points are not tracks; controls are shifted locations and may include artifacts; this is not production recall",
              "radius_px": RADIUS, "thresholds": list(THRESHOLDS),
              "caps": list(CAPS), "cases": {}}
    for name in dict.fromkeys(args.case or CONTROL_NAMES):
        archive = source/f"{name}.zip"
        if not archive.is_file():
            print(f"{name}: locally cached ZIP missing", flush=True)
            output["cases"][name] = {"status": "MISSING_ARCHIVE"}
            continue
        selected = identify_c3_training(archive)["selected"]
        if len(selected) < 5:
            output["cases"][name] = {"status": "INSUFFICIENT_LABELS"}
            continue
        print(f"\n=== {name}: {len(selected)} labelled C3 images ===", flush=True)
        with tempfile.TemporaryDirectory(prefix="c3_failures_", dir="/dev/shm") as t:
            paths = stage_512(archive, selected, Path(t))
            images = [load_gray(p) for p in paths]
            measured = analyze(images, truth_at_512(selected))
        output["cases"][name] = {"status": "OK", "results": measured}
        for threshold, res in measured.items():
            f = res["failure_counts"]
            print(f" threshold={threshold}: threshold-missing={f['threshold_missing']}, "
                  f"spatial700-discarded={f['lost_by_spatial_700']}, "
                  f"3000-cap-rescues={f['recovered_if_spatial_3000']}", flush=True)
            for stage in ("raw", "global_700", "spatial_700",
                          "spatial_1500", "spatial_3000", "spatial_6000"):
                m = res["metrics"][stage]
                print(f"   {stage:<16} truth {m['truth_hits']:>2}/{m['truth_total']}"
                      f" ({m['truth_pct']}%) | controls "
                      f"{m['control_hits']}/{m['control_total']} ({m['control_pct']}%)",
                      flush=True)
    dest = (args.output or project/"results"/"sungrazer_c3_512_truth_replay"/
            "failure_mode_audit.json").expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(output, indent=2)+"\n", encoding="utf-8")
    print(f"\nSaved: {dest}")
    print("No GPU, network, production detector or cached input changes.")


if __name__ == "__main__":
    main()
