#!/usr/bin/env python3
"""Offline position-blind probe for the active science rectangle inside CCOR JPEGs.

NOAA CCOR L3 JPEG files can include borders/labels around the nominal 1024x960
science raster. This DIAGNOSTIC uses temporal pixel variation to rank possible
1024x960 rectangles within the original JPEG canvas. The result is only a
hypothesis: labels and processing differences can fool a variation score.

Does not read reported comet positions, alter the hunter's preprocessing,
fetch remote imagery, touch production results, or send alerts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ccor_blind_jpeg_replay import CASES, validate_jpeg

EXPECTED_SCIENCE_XY = (1024, 960)
MAX_INPUT_FRAMES = 16
PROBE_FRAMES = 8


def temporal_activity(images):
    """Robust variation, suppressing isolated JPEG compression spikes."""
    if len(images) < 4:
        raise ValueError("Need at least 4 independent images")
    shape = images[0].shape
    if any(a.shape != shape for a in images):
        raise ValueError("JPEG dimensions inconsistent")
    stack = np.stack(images).astype(np.float32, copy=False)
    # Robust time-median residual rather than a single running difference.
    baseline = np.median(stack, axis=0)
    return np.median(np.abs(stack - baseline), axis=0).astype(np.float32)


def summed_area(a):
    """Zero-padded integral image, for inclusive-exclusive rectangle sums."""
    s = np.pad(a.astype(np.float64), ((1, 0), (1, 0)))
    return s.cumsum(axis=0).cumsum(axis=1)


def rect_sum(s, left, top, width, height):
    right = left + width
    bottom = top + height
    return float(s[bottom, right] - s[top, right] -
                 s[bottom, left] + s[top, left])


def rank_regions(activity, region_xy=EXPECTED_SCIENCE_XY,
                 stride=4, minimum_separation=20, topn=5):
    """Rank fixed-size regions by independent temporal-image activity."""
    h, w = activity.shape
    rw, rh = region_xy
    if not (0 < rw <= w and 0 < rh <= h):
        raise ValueError("Science rectangle cannot fit inside JPEG canvas")
    if stride < 1:
        raise ValueError("stride must be positive")
    sat = summed_area(activity)
    total = float(activity.astype(np.float64).sum())
    inside_n = rw * rh
    outside_n = w * h - inside_n
    if outside_n <= 0:
        raise ValueError("Need a border around the proposed science rectangle")
    candidates = []
    xs = sorted(set(list(range(0, w-rw+1, stride)) + [w-rw]))
    ys = sorted(set(list(range(0, h-rh+1, stride)) + [h-rh]))
    for y in ys:
        for x in xs:
            inside = rect_sum(sat, x, y, rw, rh)
            a = inside / inside_n
            b = max(0.0, total-inside) / outside_n
            ratio = (a + 1e-6) / (b + 1e-6)
            candidates.append({
                "left": x, "top": y,
                "width": rw, "height": rh,
                "mean_activity_inside": round(a, 6),
                "mean_activity_outside": round(b, 6),
                "inside_to_outside_activity_ratio": round(ratio, 4),
                "_score": inside,
            })
    candidates.sort(key=lambda d: d["_score"], reverse=True)
    chosen = []
    for item in candidates:
        if all(abs(item["left"]-r["left"]) +
               abs(item["top"]-r["top"]) >= minimum_separation
               for r in chosen):
            chosen.append({k:v for k,v in item.items() if k!="_score"})
        if len(chosen) >= topn:
            break
    return chosen


def normalized_activity_image(activity):
    high = float(np.quantile(activity, 0.995))
    if high <= 0:
        return Image.new("RGB", (activity.shape[1], activity.shape[0]),
                         (0, 0, 0))
    vals = np.uint8(np.clip(activity / high, 0, 1) * 255)
    return Image.fromarray(vals, "L").convert("RGB")


def draw_regions(img, rows):
    rendered = img.copy().convert("RGB")
    colors = [(255, 220, 35), (40, 235, 175), (252, 90, 140),
              (65, 175, 255), (255, 165, 52)]
    overlay = ImageDraw.Draw(rendered)
    for rank, rec in enumerate(rows):
        c = colors[rank % len(colors)]
        box = (rec["left"], rec["top"],
               rec["left"]+rec["width"]-1,
               rec["top"]+rec["height"]-1)
        overlay.rectangle(box, outline=c, width=4 if rank == 0 else 2)
        overlay.text((box[0]+8, box[1]+8+rank*16),
                     str(rank+1), fill=c)
    return rendered


def blind_images(root, case):
    manifest_path = root / ("blind_jpeg_" + case + ".json")
    baseline = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (baseline.get("status") != "POSITION_BLIND_REPLAY_COMPLETE"
            or baseline.get("camera") != CASES[case]["camera"]
            or not baseline.get("blind_to_report_positions")):
        raise ValueError("Invalid saved position-blind benchmark manifest")
    good = [r for r in baseline["images"] if r.get("status") == "VALIDATED"]
    if not 4 <= len(good) <= MAX_INPUT_FRAMES:
        raise ValueError("Unexpected number of archived reference JPEGs")
    paths = []
    for rec in good:
        name = rec["name"]
        if Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("Invalid JPEG basename")
        p = root / case / "jpegs" / name
        if validate_jpeg(p)["sha256"] != rec["sha256"]:
            raise ValueError("JPEG bytes no longer match the saved benchmark: "+name)
        paths.append(p)
    idxs = np.unique(np.linspace(0, len(paths)-1,
                                 min(PROBE_FRAMES, len(paths))).astype(int))
    return [paths[int(i)] for i in idxs]


def run_case(case, root, output_dir):
    paths = blind_images(root, case)
    images = []
    for p in paths:
        with Image.open(p) as im:
            images.append(np.asarray(im.convert("L"), dtype=np.uint8))
    activity = temporal_activity(images)
    suggestions = rank_regions(activity)
    if not suggestions:
        raise ValueError("No candidate field regions")
    report = {
        "mode": "POSITION_BLIND_CCOR_JPEG_SCIENCE_FIELD_PROBE",
        "case": case,
        "camera": CASES[case]["camera"],
        "canvas_shape_xy": [activity.shape[1], activity.shape[0]],
        "nominal_science_shape_xy": list(EXPECTED_SCIENCE_XY),
        "sampled_jpeg_filenames": [p.name for p in paths],
        "sampled_jpeg_sha256": [
            hashlib.sha256(p.read_bytes()).hexdigest() for p in paths],
        "position_information_used": False,
        "science_field_geometry_confirmed": False,
        "candidate_rectangles": suggestions,
        "network_access": False,
        "production_modified": False,
        "limitations": [
            "Temporal-activity score is not independent astrometric registration.",
            "Canvas labels, changing annotations, and JPEG rendering may bias the score.",
            "A nominal 1024x960 science rectangle may be rescaled when plotted.",
            "Image intensities from FITS level 1 and JPEG level 3 are not directly comparable.",
            "Do not crop or change production processing based on this report alone.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / (case + "_jpeg_roi_probe.json")
    if dest.exists():
        raise FileExistsError("Refusing to overwrite prior probe: "+str(dest))
    with Image.open(paths[len(paths)//2]) as src:
        overlay = draw_regions(src, suggestions)
    heat = draw_regions(normalized_activity_image(activity), suggestions)
    overlay_name = case+"_candidate_field_rectangles.png"
    heat_name = case+"_temporal_activity_map.png"
    overlay.save(output_dir / overlay_name)
    heat.save(output_dir / heat_name)
    report["canvas_overlay"] = str(output_dir / overlay_name)
    report["temporal_activity_map"] = str(output_dir / heat_name)
    dest.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    top = suggestions[0]
    print(f"{case}: canvas={tuple(report['canvas_shape_xy'])} "
          f"hypothesis=({top['left']},{top['top']}) "
          f"size={tuple(EXPECTED_SCIENCE_XY)} "
          f"activity_ratio={top['inside_to_outside_activity_ratio']:.3f}",
          flush=True)
    print("  Images:", output_dir / overlay_name,
          "|", output_dir / heat_name, flush=True)
    print("  Report:", dest, flush=True)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-dir", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--case", choices=tuple(CASES), action="append")
    args = p.parse_args()
    source = args.source_dir.expanduser().resolve()
    output = args.output_dir.expanduser().resolve()
    if (not source.is_dir() or source == output or
            source in output.parents or output in source.parents or
            "comet_hunter_native_cuda_v7_archive" in output.parts):
        p.error("Separate non-production output and existing source required")
    failed = False
    for case in dict.fromkeys(args.case or CASES):
        try:
            run_case(case, source, output)
        except Exception as exc:
            failed = True
            print(f"{case}: FAILED: {type(exc).__name__}: {exc}", flush=True)
    if failed:
        raise SystemExit(2)
    print("Offline JPEG-field probe finished. No registration or comet claim.")


if __name__ == "__main__":
    main()
