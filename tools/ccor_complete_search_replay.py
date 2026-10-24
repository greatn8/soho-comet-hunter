#!/usr/bin/env python3
"""Offline CCOR complete-search/legacy A-B replay on saved NOAA JPEGs.

Uses cached position-blind benchmark frames only. Does not fetch images, read
reported comet positions, change production caches, run watchers, or send alerts.
Search/rank evaluation only: NOT evidence of comet recovery or precision.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import ccor_hunter as hunter
from ccor_blind_jpeg_replay import CASES, detector_fingerprint, validate_jpeg


def scored_row(row, rank, original_ids):
    return {
        "rank": rank,
        "id": row["id"],
        "in_legacy_first_40": row["id"] in original_ids,
        "frames": row["frames"],
        "first": row["first"],
        "last": row["last"],
        "rms_px": row["rms_px"],
        "speed_px_h": row["speed_px_h"],
        "sunward_px_h": row["sunward_px_h"],
        "points": row.get("points", []),
        "x_first": row["x_first"],
        "y_first": row["y_first"],
        "vx_px_h": row["vx_px_h"],
        "vy_px_h": row["vy_px_h"],
    }


def summarize(legacy, global_tracks, legacy_meta, global_meta):
    """Never confuse additional motion hypotheses with confirmed comets."""
    old_ids = {r["id"] for r in legacy}
    new_ids = {r["id"] for r in global_tracks}
    legacy_ranked = sorted(legacy, key=hunter.track_quality, reverse=True)
    return {
        "legacy_hypotheses": len(legacy),
        "complete_search_top40_hypotheses": len(global_tracks),
        "legacy_search": legacy_meta,
        "complete_search": global_meta,
        "new_top40_hypothesis_ids": sorted(new_ids - old_ids),
        "new_top40_count": len(new_ids - old_ids),
        "legacy_only_count": len(old_ids - new_ids),
        "same_hypothesis_ids": len(new_ids & old_ids),
        "top20_complete_search": [
            scored_row(r, i, old_ids) for i, r in enumerate(global_tracks[:20], 1)
        ],
        "top20_legacy_ranked": [
            scored_row(r, i, old_ids) for i, r in enumerate(legacy_ranked[:20], 1)
        ],
        "all_complete_search_top40": [
            scored_row(r, i, old_ids) for i, r in enumerate(global_tracks, 1)
        ],
        "scan_completed": bool(global_meta["scan_completed"]),
        "status": ("COMPLETE_AB_COMPARISON"
                   if global_meta["scan_completed"]
                   else "INCOMPLETE_SEARCH_BUDGET"),
        "reported_comet_recovered": None,
        "limitations": [
            "Motion hypotheses are not individually verified comet objects.",
            "The 70-peaks-per-frame detector cap remains active.",
            "Comet recall cannot be measured without independent FITS/JPEG registration.",
            "New top-40 motion tracks do not by themselves prove improved sensitivity.",
        ],
    }


def replay_case(case_name, source_dir, output_dir):
    case = CASES[case_name]
    camera = case["camera"]
    manifest_path = source_dir / ("blind_jpeg_" + case_name + ".json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("mode") != "POSITION_BLIND_ACTUAL_PRODUCTION_CCOR_JPEG_DETECTOR"
            or manifest.get("status") != "POSITION_BLIND_REPLAY_COMPLETE"
            or not manifest.get("blind_to_report_positions")
            or manifest.get("camera") != camera):
        raise ValueError("Missing valid position-blind reference replay")
    # The opt-in complete-search feature changed the detector source hash.
    # Output parity, not matching hashes, establishes legacy reproducibility.
    same_source_hash = (
        manifest.get("detector_source_sha256") == detector_fingerprint()
    )
    good = [r for r in manifest.get("images", [])
            if r.get("status") == "VALIDATED"]
    if len(good) != manifest.get("frame_count") or len(good) < 6:
        raise ValueError("Reference JPEG manifest is incomplete")
    cache = source_dir / case_name / "jpegs"
    for row in good:
        name = row["name"]
        if (Path(name).name != name or "/" in name or "\\" in name
                or hunter.STAMP_RE.fullmatch(name) is None
                or ("_" + camera + "_") not in name.lower()):
            raise ValueError("Unsafe JPEG filename: " + name)
        if validate_jpeg(cache / name)["sha256"] != row["sha256"]:
            raise ValueError("Reference JPEG hash mismatch: " + name)
    start = dt.datetime.fromisoformat(case["from_utc"].replace("Z", "+00:00"))
    end = dt.datetime.fromisoformat(case["through_utc"].replace("Z", "+00:00"))
    images, times, sizes = hunter.load_frames(
        cache, camera, end, hours=(end - start).total_seconds() / 3600 + .01
    )
    if (len(images) != len(good)
            or [hunter.iso(t) for t in times] != manifest.get("processed_times_utc")):
        raise ValueError("Loaded image sequence differs from saved benchmark")
    legacy, legacy_meta = hunter.track_points(
        images, times, sizes, camera, return_diagnostics=True
    )
    legacy_ranked = sorted(legacy, key=hunter.track_quality, reverse=True)
    expected = [{k: v for k, v in row.items() if k != "blind_rank"}
                for row in manifest.get("ranked_tracks", [])]
    if legacy_ranked != expected:
        raise ValueError("Legacy replay differs from original position-blind output")
    global_tracks, global_meta = hunter.track_points(
        images, times, sizes, camera, complete_search=True,
        return_diagnostics=True
    )
    report = {
        "mode": "CCOR_POSITION_BLIND_REAL_JPEG_COMPLETE_SEARCH_AB",
        "case": case_name,
        "camera": camera,
        "source_level": "NOAA_L3_JPEG",
        "source_manifest": str(manifest_path),
        "current_detector_sha256": detector_fingerprint(),
        "saved_baseline_detector_sha256": manifest.get("detector_source_sha256"),
        "detector_source_hash_matches_saved_baseline": same_source_hash,
        "frames": len(images),
        "actual_jpeg_dimensions": list(sizes[0]),
        "network_access": False,
        "production_modified": False,
        "baseline_reproduced_exactly": True,
        "positions_used_to_generate_candidates": False,
        **summarize(legacy, global_tracks, legacy_meta, global_meta),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / ("complete_search_ab_" + case_name + ".json")
    if dest.exists():
        raise FileExistsError("Refusing to overwrite earlier report: " + str(dest))
    dest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{case_name}: frames={len(images)} legacy={len(legacy)} "
          f"complete_top40={len(global_tracks)} "
          f"total_hypotheses={global_meta['distinct_hypotheses']} "
          f"new_top40={report['new_top40_count']} "
          f"scan_completed={report['scan_completed']} status={report['status']}",
          flush=True)
    print(f"  Exact legacy parity: YES | detector hash unchanged: "
          f"{same_source_hash} | report: {dest}", flush=True)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-dir", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--case", action="append", choices=tuple(CASES))
    a = p.parse_args()
    source = a.source_dir.expanduser().resolve()
    output = a.output_dir.expanduser().resolve()
    if not source.is_dir():
        p.error("Existing position-blind JPEG directory not found")
    if source == output or source in output.parents or output in source.parents:
        p.error("Output must be separate from input and not contain input")
    if "comet_hunter_native_cuda_v7_archive" in output.parts:
        p.error("Refusing production-project output directory")
    failed = False
    for case in dict.fromkeys(a.case or CASES):
        try:
            result = replay_case(case, source, output)
            failed |= result["status"] != "COMPLETE_AB_COMPARISON"
        except Exception as exc:
            failed = True
            print(f"{case}: FAILED: {type(exc).__name__}: {exc}", flush=True)
    if failed:
        raise SystemExit(2)
    print("Offline real-JPEG A/B complete. No comet recovery claim.")


if __name__ == "__main__":
    main()
