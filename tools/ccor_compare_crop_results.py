#!/usr/bin/env python3
"""Compare saved full-frame and provisional-crop CCOR A/B reports.

Matches fitted tracks in ORIGINAL JPEG coordinates and time, not hashed track IDs
(which necessarily change with cropping). Approximate matches are diagnostic:
they are not identification of individual objects or confirmed comets.
Reads only JSON. Does not download, modify live services, or write outputs.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path


def parse_time(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def to_source_coordinates(row, report, variant, when):
    """Map fitted x/y and velocity onto the full JPEG presentation canvas."""
    source_width, source_height = report["source_frames"][0]["jpeg_size"]
    if variant == "full":
        h, w = report["raw_full_frame_shape_yx"]
        offset_x = offset_y = 0.0
        span_w, span_h = source_width, source_height
    elif variant == "crop":
        h, w = report["crop_processing_shape_yx"]
        offset_x, offset_y, span_w, span_h = report["crop_xywh"]
    else:
        raise ValueError("Unknown variant: " + variant)
    age_hours = (when - parse_time(row["first"])).total_seconds() / 3600
    return (
        offset_x + (row["x_first"] + row["vx_px_h"] * age_hours) * span_w / w,
        offset_y + (row["y_first"] + row["vy_px_h"] * age_hours) * span_h / h,
    )


def distance_at(a, b, report, when):
    x1, y1 = to_source_coordinates(a, report, "full", when)
    x2, y2 = to_source_coordinates(b, report, "crop", when)
    return math.hypot(x1 - x2, y1 - y2)


def match_top20(report, max_distance=8.0, min_overlap_minutes=30):
    """Greedy one-to-one matching, constrained by simultaneous track intervals."""
    original = report["full"]["complete_top20"]
    cropped = report["crop"]["complete_top20"]
    candidate_pairs = []
    for i, a in enumerate(original):
        for j, b in enumerate(cropped):
            start = max(parse_time(a["first"]), parse_time(b["first"]))
            end = min(parse_time(a["last"]), parse_time(b["last"]))
            if (end - start).total_seconds() < 60 * min_overlap_minutes:
                continue
            d_start = distance_at(a, b, report, start)
            d_end = distance_at(a, b, report, end)
            separation = max(d_start, d_end)
            if separation <= max_distance:
                candidate_pairs.append((separation, i, j, d_start, d_end))
    candidate_pairs.sort()
    used_full = set()
    used_crop = set()
    matched = []
    for separation, i, j, d_start, d_end in candidate_pairs:
        if i in used_full or j in used_crop:
            continue
        used_full.add(i)
        used_crop.add(j)
        matched.append({
            "full_rank": i + 1,
            "crop_rank": j + 1,
            "full_frames": original[i]["frames"],
            "crop_frames": cropped[j]["frames"],
            "max_separation_source_jpeg_px": round(separation, 2),
        })
    matched.sort(key=lambda item: item["crop_rank"])
    return {
        "matches": matched,
        "matched_count": len(matched),
        "unmatched_full_ranks": [i + 1 for i in range(len(original))
                                 if i not in used_full],
        "unmatched_crop_ranks": [j + 1 for j in range(len(cropped))
                                 if j not in used_crop],
        "match_tolerance_source_jpeg_px": max_distance,
        "minimum_time_overlap_minutes": min_overlap_minutes,
    }


def summarize(report):
    if (report.get("mode") != "OFFLINE_CCOR_SCIENCE_CROP_AB"
            or not report.get("same_input_frames")
            or report.get("production_modified") is not False):
        raise ValueError("Not a valid standalone CCOR crop A/B result")
    original = report["full"]
    cropped = report["crop"]
    geom = match_top20(report)
    print(f"\n{report['camera'].upper()}: {report['frame_count']} identical frame times")
    print(f"  Full JPEG: {report['source_frames'][0]['jpeg_size']}, "
          f"processed {report['raw_full_frame_shape_yx']}")
    print(f"  Crop {report['crop_xywh']}, processed "
          f"{report['crop_processing_shape_yx']}")
    for label, data in (("FULL", original), ("CROP", cropped)):
        completed = data["complete_search"]["scan_completed"]
        print(f"  {label}: legacy={data['legacy_track_count']} "
              f"complete_top40={data['complete_top40_track_count']} "
              f"total_hypotheses={data['complete_search']['distinct_hypotheses']} "
              f"new_vs_legacy={data['new_hypothesis_ids_in_complete_top40']} "
              f"inside_crop_legacy={data['hypotheses_with_endpoints_inside_crop']['legacy']} "
              f"inside_crop_complete={data['hypotheses_with_endpoints_inside_crop']['complete_top40']} "
              f"full_scan={completed}")
    print("  Approximate matched tracks among top20: "
          f"{geom['matched_count']} / 20 at <=8 source JPEG px")
    print("  Cropped top20 unmatched ranks: "
          f"{geom['unmatched_crop_ranks']}")
    print("  Full top20 unmatched ranks: "
          f"{geom['unmatched_full_ranks']}")
    print("  Top20 matched rank comparisons (full -> crop):")
    for item in geom["matches"][:12]:
        print(f"    {item['full_rank']:>2} -> {item['crop_rank']:>2} "
              f"({item['max_separation_source_jpeg_px']} px max)")
    if not (original["complete_search"]["scan_completed"]
            and cropped["complete_search"]["scan_completed"]):
        print("  WARNING: Incomplete scan; cannot compare global top40 ranking.")
    print("  CAUTION: Rank/count/geometry differences do NOT measure "
          "comet recall or precision.")
    return geom


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.input_dir.expanduser().resolve()
    if not root.is_dir():
        parser.error("Report directory does not exist")
    for camera in ("ccor1", "ccor2"):
        path = root / (camera + "_science_crop_ab.json")
        if not path.is_file():
            parser.error("Missing report " + str(path))
        summarize(json.loads(path.read_text(encoding="utf-8")))
    print("\nNo production state changed. No claimed comet detections.")


if __name__ == "__main__":
    main()
