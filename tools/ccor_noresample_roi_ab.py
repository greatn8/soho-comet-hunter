#!/usr/bin/env python3
"""Controlled offline CCOR full-image vs science-ROI-only experiment.

This experiment keeps the SAME decoded arrays, pixel scale, time axis,
median baseline, temporal-residual threshold, central occulting mask, motion
gates, and ranking algorithm in both arms. The only difference is whether
the brightest 70 local peaks are selected across the full presentation image
or only within the proposed science rectangle. Unlike an image crop this
does not resample pixels or shift the occulting mask.

No network, live cache writes, process changes, or comet recovery claims.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np

import ccor_hunter as h

SCIENCE_ROI = (90, 123, 1024, 960)


def extract_spots(frames, source_sizes, camera, roi=SCIENCE_ROI):
    """Return exact baseline peak lists and peak lists after ROI selection."""
    if len(frames) < 6:
        raise ValueError("At least six identical-frame images required")
    if len(set(source_sizes)) != 1:
        raise ValueError("Mixed source JPEG dimensions cannot be compared")
    original_w, original_h = source_sizes[0]
    left, top, rw, rh = roi
    if min(left, top) < 0 or left + rw > original_w or top + rh > original_h:
        raise ValueError("ROI does not fit source JPEG dimensions")
    # Same calculation as h.track_points, restricted to its last 16 frames.
    baseline = np.median(np.stack(frames), axis=0)
    start = max(0, len(frames) - 16)
    full = []
    masked = []
    per_frame = []
    for index in range(start, len(frames)):
        frame = frames[index]
        height, width = frame.shape
        # h.peaks already applies the existing robust residual threshold,
        # central occulting mask and strict local-maximum condition.
        # An uncapped list is then spatially filtered BEFORE selecting top 70.
        spots_all = h.peaks(frame, baseline, camera, limit=frame.size)
        unmasked = spots_all[:70]
        x0, x1 = left * width / original_w, (left + rw) * width / original_w
        y0, y1 = top * height / original_h, (top + rh) * height / original_h
        eligible = [p for p in spots_all
                    if x0 <= p[0] < x1 and y0 <= p[1] < y1]
        clipped = eligible[:70]
        # Fail closed if the prep code isn't exactly the legacy baseline.
        if unmasked != h.peaks(frame, baseline, camera, limit=70):
            raise AssertionError("Baseline peak-selection parity failure")
        full.append(unmasked)
        masked.append(clipped)
        per_frame.append({
            "index": index,
            "all_eligible_local_peaks": len(spots_all),
            "science_roi_local_peaks": len(eligible),
            "full_selected_70": len(unmasked),
            "roi_selected_70": len(clipped),
            "full_top70_outside_roi": sum(
                not (x0 <= p[0] < x1 and y0 <= p[1] < y1) for p in unmasked
            ),
        })
    return full, masked, per_frame, start


def run_detector(frames, times, source_sizes, camera, prepared_spots, start):
    # In this process only, replace peak *lookup* with the precalculated
    # positions. h.track_points still performs the unmodified fitting,
    # duplicate suppression, speed gating and both ranking algorithms.
    refs = {id(frames[start + i]): spots
            for i, spots in enumerate(prepared_spots)}
    def cached_peaks(frame, baseline, requested_camera, limit=70):
        if requested_camera != camera or limit != 70 or id(frame) not in refs:
            raise AssertionError("Unexpected peak lookup during offline replay")
        return refs[id(frame)]
    with patch.object(h, "peaks", cached_peaks):
        legacy, legacy_meta = h.track_points(
            frames, times, source_sizes, camera, return_diagnostics=True)
        complete, complete_meta = h.track_points(
            frames, times, source_sizes, camera, complete_search=True,
            return_diagnostics=True)
    return legacy, legacy_meta, complete, complete_meta


def pack_tracks(tracks):
    return [{
        "rank": index,
        "id": item["id"],
        "first": item["first"], "last": item["last"],
        "frames": item["frames"], "rms_px": item["rms_px"],
        "speed_px_h": item["speed_px_h"],
        "x_first": item["x_first"], "y_first": item["y_first"],
        "vx_px_h": item["vx_px_h"], "vy_px_h": item["vy_px_h"],
    } for index, item in enumerate(tracks, 1)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path,
                        help="Existing production CCOR JPEG cache parent (read-only)")
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="New independent results folder")
    parser.add_argument("--hours", type=float, default=5.0)
    parser.add_argument("--camera", action="append", choices=h.CAMERAS)
    parser.add_argument("--end-utc",
                        help="Optional fixed UTC end time (e.g. 2026-10-24T05:00:00Z)")
    args = parser.parse_args()
    source = args.source_dir.expanduser().resolve()
    output = args.output_dir.expanduser().resolve()
    if not source.is_dir() or not 1 <= args.hours <= 8:
        parser.error("Source directory missing or hours outside 1..8")
    if output == source or output in source.parents or source in output.parents:
        parser.error("Output must be separate from cached image directory")
    if "comet_hunter_native_cuda_v7_archive" in output.parts:
        parser.error("Refusing to write inside active production project")
    if output.exists() and any(output.iterdir()):
        parser.error("Output directory must be empty")
    cameras = list(dict.fromkeys(args.camera or h.CAMERAS))
    end_fixed = (dt.datetime.fromisoformat(args.end_utc.replace("Z", "+00:00"))
                 if args.end_utc else None)
    if end_fixed is not None and end_fixed.utcoffset() is None:
        parser.error("--end-utc must include Z or a UTC offset")
    output.mkdir(parents=True, exist_ok=True)
    failures = 0
    for camera in cameras:
        try:
            cache = source / camera
            paths = sorted(cache.glob(f"*_{camera}_*by*.jpg"))
            if not paths:
                raise ValueError("No cached JPEGs")
            end = end_fixed or max(h.stamp(p.name) for p in paths)
            frames, times, sizes = h.load_frames(cache, camera, end, args.hours)
            if len(frames) < 6:
                raise ValueError("Insufficient frames to replay detector")
            if len(set(sizes)) != 1:
                raise ValueError("Inconsistent source JPEG geometry")
            full_spots, roi_spots, observations, start = extract_spots(
                frames, sizes, camera
            )
            old, old_meta, baseline, base_meta = run_detector(
                frames, times, sizes, camera, full_spots, start)
            new_old, new_old_meta, roi, roi_meta = run_detector(
                frames, times, sizes, camera, roi_spots, start)
            if not base_meta["scan_completed"] or not roi_meta["scan_completed"]:
                raise ValueError("Complete search hit safety budget; not comparable")
            old_ids = {item["id"] for item in baseline}
            new_ids = {item["id"] for item in roi}
            report = {
                "mode": "OFFLINE_CCOR_NO_RESAMPLE_ROI_PEAK_FILTER_AB",
                "camera": camera, "production_modified": False,
                "network_access": False, "comet_recovery": None,
                "source_image_xywh": [0, 0, *sizes[0]],
                "roi_xywh": list(SCIENCE_ROI),
                "full_and_roi_frames_are_identical": True,
                "resampled_or_cropped_images": False,
                "processed_frames": len(frames),
                "selected_peak_frames": len(full_spots),
                "window_start_utc": h.iso(times[0]),
                "window_end_utc": h.iso(times[-1]),
                "frame_pixel_sha256": [
                    hashlib.sha256(frame.tobytes()).hexdigest()
                    for frame in frames
                ],
                "selected_peak_observations": observations,
                "baseline": {
                    "legacy_count": len(old),
                    "complete_count": len(baseline),
                    "legacy_info": old_meta, "complete_info": base_meta,
                    "top40": pack_tracks(baseline),
                },
                "roi": {
                    "legacy_count": len(new_old),
                    "complete_count": len(roi),
                    "legacy_info": new_old_meta, "complete_info": roi_meta,
                    "top40": pack_tracks(roi),
                },
                "exact_top40_matching_ids": len(old_ids & new_ids),
                "roi_new_top40_ids": len(new_ids - old_ids),
                "roi_displaced_full_top40_ids": len(old_ids - new_ids),
                "full_top20_retained_in_roi_top40": len(
                    {x["id"] for x in baseline[:20]} & new_ids),
                "same_exact_top1": bool(baseline and roi
                                        and baseline[0]["id"] == roi[0]["id"]),
                "original_top70_peaks_outside_roi_total": sum(
                    item["full_top70_outside_roi"] for item in observations),
                "limitations": [
                    "Science ROI is provisional; not instrument astrometry.",
                    "Only exclusion of points in the presentation border is tested.",
                    "The original central mask, thresholds and motion logic are unchanged.",
                    "Hypothesis counts/overlap do not establish actual comet recall.",
                    "This replay does not alter the 70-peaks-per-frame budget.",
                ],
            }
            dest = output / (camera + "_roi_mask_ab.json")
            dest.write_text(json.dumps(report, indent=2) + "\n",
                            encoding="utf-8")
            print(f"{camera.upper()}: frames={len(frames)}, "
                  f"full hypotheses={base_meta['distinct_hypotheses']}, "
                  f"ROI hypotheses={roi_meta['distinct_hypotheses']}, "
                  f"shared top40={report['exact_top40_matching_ids']}/40, "
                  f"new ROI top40={report['roi_new_top40_ids']}, "
                  f"outside top70 peaks={report['original_top70_peaks_outside_roi_total']} "
                  f"| {dest}", flush=True)
        except Exception as exc:
            failures += 1
            print(f"{camera.upper()}: ERROR {type(exc).__name__}: {exc}",
                  flush=True)
    if failures:
        raise SystemExit(2)
    print("No-resample ROI-only A/B complete. Live CCOR remains unchanged.")


if __name__ == "__main__":
    main()
