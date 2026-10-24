#!/usr/bin/env python3
"""Offline, read-only CCOR science-crop A/B on locally cached NOAA L3 JPEGs.

Run the exact existing detector on identical time windows: unaltered JPEG
baseline versus a provisional 1024x960 science-field crop. Compare both the
legacy first-40 and opt-in full seed search. No downloads, production writes,
alerts, GPU jobs, worker restarts, or claims of confirmed comet recovery.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import ccor_hunter as hunter

CROP = (90, 123, 1024, 960)  # provisional; NOT an astrometric calibration
UTC = dt.timezone.utc


def crop_frame(path, original_size, crop=CROP):
    left, top, width, height = crop
    with Image.open(path) as image:
        if image.size != tuple(original_size):
            raise ValueError("Source image dimensions changed: " + str(path))
        if min(left, top) < 0 or left + width > image.width or top + height > image.height:
            raise ValueError(f"Crop {crop} is outside JPEG {image.size}: {path.name}")
        gray = image.convert("L").crop(
            (left, top, left + width, top + height)
        )
        factor = min(1.0, 512.0 / width)
        output_size = (round(width * factor), round(height * factor))
        result = np.asarray(
            gray.resize(output_size, Image.Resampling.BOX), dtype=np.float32
        )
    return result


def load_pair(directory, camera, hours):
    """Use production's image list, timestamps, resizing for the baseline."""
    filenames = sorted(directory.glob(f"*_{camera}_*by*.jpg"))
    if not filenames:
        raise ValueError(f"No cached {camera} JPEGs in {directory}")
    end = max(hunter.stamp(p.name) for p in filenames)
    original, times, source_sizes = hunter.load_frames(
        directory, camera, end, hours=hours
    )
    if len(original) < 6:
        raise ValueError(f"{camera}: only {len(original)} usable frames; need >=6")
    path_by_stamp = {}
    for path in filenames:
        when = hunter.stamp(path.name)
        if when in path_by_stamp:
            raise ValueError(f"Duplicate {camera} frame timestamp: {when}")
        path_by_stamp[when] = path
    selected = [path_by_stamp[when] for when in times]
    cropped = [
        crop_frame(path, source_sizes[i]) for i, path in enumerate(selected)
    ]
    if len({tuple(arr.shape) for arr in cropped}) != 1:
        raise ValueError("Cropped frames have inconsistent shapes")
    fingerprints = [
        {"filename": path.name, "time_utc": hunter.iso(times[i]),
         "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
         "jpeg_size": list(source_sizes[i])}
        for i, path in enumerate(selected)
    ]
    return original, cropped, times, source_sizes, fingerprints, end


def within_crop(row, source_size, mode, crop=CROP):
    """Diagnostic only: are first and last fitted points inside provisional ROI?"""
    left, top, width, height = crop
    native_width, native_height = source_size
    shape = row.get("processing_shape") or [512, 512]
    w = float(shape[0])
    h = float(shape[1])
    def native(x, y):
        if mode == "full":
            factor = min(1.0, 512.0 / native_width)
            return x / factor, y / factor
        factor = min(1.0, 512.0 / width)
        return left + x / factor, top + y / factor
    points = [native(float(row[kx]), float(row[ky]))
              for kx, ky in (("x_first", "y_first"), ("x_last", "y_last"))]
    return all(left <= x < left + width and top <= y < top + height
               and 0 <= x < native_width and 0 <= y < native_height
               for x, y in points)


def evaluate(frames, times, sizes, camera, mode):
    # Run both production algorithms, without modifying either.
    legacy, legacy_info = hunter.track_points(
        frames, times, sizes, camera, return_diagnostics=True
    )
    complete, complete_info = hunter.track_points(
        frames, times, sizes, camera,
        complete_search=True, return_diagnostics=True
    )
    legacy_sorted = sorted(legacy, key=hunter.track_quality, reverse=True)
    full_ids = {row["id"] for row in complete}
    first_ids = {row["id"] for row in legacy}
    def snapshot(rows):
        return [
            {"id": r["id"], "rank": i, "frames": r["frames"],
             "rms_px": r["rms_px"], "speed_px_h": r["speed_px_h"],
             "first": r["first"], "last": r["last"],
             "x_first": r["x_first"], "y_first": r["y_first"],
             "vx_px_h": r["vx_px_h"], "vy_px_h": r["vy_px_h"],
             "inside_provisional_crop": within_crop(r, sizes[0], mode)}
            for i, r in enumerate(rows, 1)
        ]
    return {
        "legacy_search": legacy_info,
        "complete_search": complete_info,
        "legacy_track_count": len(legacy),
        "complete_top40_track_count": len(complete),
        "new_hypothesis_ids_in_complete_top40": (
            len(full_ids - first_ids) if complete_info["scan_completed"] else None
        ),
        "hypotheses_with_endpoints_inside_crop": {
            "legacy": sum(within_crop(r, sizes[0], mode) for r in legacy),
            "complete_top40": sum(within_crop(r, sizes[0], mode) for r in complete),
        },
        "legacy_top20": snapshot(legacy_sorted[:20]),
        "complete_top20": snapshot(complete[:20]),
        "complete_search_status": (
            "COMPLETE" if complete_info["scan_completed"] else
            "INCOMPLETE_BUDGET_EXCEEDED"
        ),
    }


def make_preview(path, dest, crop=CROP):
    with Image.open(path) as original:
        img = original.convert("RGB")
    d = ImageDraw.Draw(img)
    l, t, w, h = crop
    d.rectangle((l, t, l + w - 1, t + h - 1), outline=(255, 211, 0), width=4)
    img.save(dest)


def run_camera(source_dir, output_dir, camera, hours):
    original, cropped, times, sizes, frames, end = load_pair(
        source_dir / camera, camera, hours
    )
    print(f"{camera}: evaluating {len(original)} verified cached frames "
          f"ending {hunter.iso(end)}", flush=True)
    full_eval = evaluate(original, times, sizes, camera, "full")
    print(f"  Full JPEG: legacy={full_eval['legacy_track_count']}, "
          f"complete={full_eval['complete_top40_track_count']}, "
          f"scan={full_eval['complete_search_status']}", flush=True)
    crop_eval = evaluate(cropped, times, sizes, camera, "crop")
    print(f"  Crop JPEG: legacy={crop_eval['legacy_track_count']}, "
          f"complete={crop_eval['complete_top40_track_count']}, "
          f"scan={crop_eval['complete_search_status']}", flush=True)
    preview = output_dir / (camera + "_science_crop_preview.jpg")
    make_preview(source_dir / camera / frames[-1]["filename"], preview)
    result = {
        "mode": "OFFLINE_CCOR_SCIENCE_CROP_AB",
        "camera": camera,
        "source_product": "NOAA_CCOR_L3_JPEG",
        "detector_modified": False,
        "production_modified": False,
        "network_access": False,
        "source_dir": str(source_dir / camera),
        "crop_xywh": list(CROP),
        "crop_calibrated": False,
        "same_input_frames": True,
        "frame_count": len(original),
        "first_frame_utc": hunter.iso(times[0]),
        "last_frame_utc": hunter.iso(times[-1]),
        "raw_full_frame_shape_yx": list(original[0].shape),
        "crop_processing_shape_yx": list(cropped[0].shape),
        "source_frames": frames,
        "full": full_eval,
        "crop": crop_eval,
        "preview_jpeg": str(preview),
        "known_comet_recovered": None,
        "limitations": [
            "No independently calibrated report-coordinate-to-JPEG transformation.",
            "Hypotheses and top-40 counts are not comet recall or precision.",
            "CCOR science crop bounds remain provisional.",
            "Production detector retains the 70-bright-peaks per frame cutoff.",
            "This run leaves all production hunters, results and alerts untouched.",
        ],
    }
    report = output_dir / (camera + "_science_crop_ab.json")
    if report.exists():
        raise FileExistsError("Refusing to overwrite previous report: " + str(report))
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"  Saved {report} and {preview}", flush=True)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-dir", type=Path, required=True,
                    help="Read-only parent directory of cached ccor1/ccor2 JPEGs")
    ap.add_argument("--output-dir", type=Path, required=True,
                    help="New directory OUTSIDE production project and cached images")
    ap.add_argument("--camera", action="append", choices=hunter.CAMERAS)
    ap.add_argument("--hours", type=float, default=5.0)
    a = ap.parse_args()
    source = a.source_dir.expanduser().resolve()
    output = a.output_dir.expanduser().resolve()
    if not 1 <= a.hours <= 8:
        ap.error("--hours must be between 1 and 8")
    if not source.is_dir():
        ap.error("JPEG source directory missing")
    if output == source or output in source.parents or source in output.parents:
        ap.error("Output must be independent of input")
    if "comet_hunter_native_cuda_v7_archive" in output.parts:
        ap.error("Refusing output inside production-project directory")
    if output.exists() and any(output.iterdir()):
        ap.error("Output directory must be empty to preserve earlier runs")
    output.mkdir(parents=True, exist_ok=True)
    errors = 0
    for camera in dict.fromkeys(a.camera or hunter.CAMERAS):
        try:
            row = run_camera(source, output, camera, a.hours)
            if (row["crop"]["complete_search_status"] != "COMPLETE"
                    or row["full"]["complete_search_status"] != "COMPLETE"):
                errors += 1
        except Exception as exc:
            errors += 1
            print(f"{camera}: FAILED: {type(exc).__name__}: {exc}", flush=True)
    print("Finished offline comparison. No known-comet recovery claim.", flush=True)
    if errors:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
