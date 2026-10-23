#!/usr/bin/env python3
"""Position-blind historical replay using the ACTUAL production CCOR JPEG detector.

Stage 1 of the reported-comet benchmark. This module deliberately does not
import the independently reported comet positions. Date windows are selected
because independent reports exist, but the detector sees only image pixels,
filenames and timestamps. A separate evaluator loads coordinates AFTER the
blind candidates have been saved.

No production cache, dashboard, mail, scheduling, GPU, or hunter service touched.
JPEG sources are NOAA CCOR Level 3 images, the actual CCOR hunter input.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import inspect
import json
from pathlib import Path

from PIL import Image
import ccor_hunter as hunter

UTC = dt.timezone.utc

# Explicitly position-free case schedule. Input is not blinded in time.
CASES = {
    "ccor1-20261022-ruan": {
        "camera": "ccor1",
        "from_utc": "2026-10-22T11:30:00Z",
        "through_utc": "2026-10-22T14:30:00Z",
    },
    "ccor2-20261022-wage": {
        "camera": "ccor2",
        "from_utc": "2026-10-22T22:45:00Z",
        "through_utc": "2026-10-23T02:15:00Z",
    },
}
MAX_FRAMES = 24
MAX_JPEG_BYTES = 1_500_000


def parse_time(t):
    return dt.datetime.fromisoformat(t.replace("Z", "+00:00"))


def trusted_frame_path(directory, name, camera):
    if (Path(name).name != name or "/" in name or "\\" in name
            or hunter.STAMP_RE.fullmatch(name) is None
            or hunter.stamp(name).tzinfo is None
            or ("_"+camera+"_") not in name.lower()):
        raise ValueError("Unrecognised JPEG filename: " + name)
    return directory / name


def validate_jpeg(path):
    data = path.read_bytes()
    if not 2500 <= len(data) <= MAX_JPEG_BYTES or data[:2] != b"\xff\xd8":
        raise ValueError("Invalid JPEG signature or size")
    with Image.open(path) as img:
        img.verify()
    return {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def load_local(directory, camera, start, end):
    selected = []
    for path in directory.glob("*.jpg"):
        try:
            trusted_frame_path(directory, path.name, camera)
            when = hunter.stamp(path.name)
            if start <= when <= end:
                selected.append({"name": path.name, "time": hunter.iso(when),
                                 "source": "LOCAL_JPEG", "path": str(path)})
        except (ValueError, OSError):
            pass
    return sorted(selected, key=lambda e: e["name"])


def acquire_frames(case, cache, *, no_download=False, fetch=None,
                   max_frames=MAX_FRAMES):
    """Cache only bounded NOAA JPEG frames; never touch live hunter cache."""
    camera = case["camera"]
    start, end = parse_time(case["from_utc"]), parse_time(case["through_utc"])
    cache.mkdir(parents=True, exist_ok=True)
    errors = []
    selected = []
    if no_download:
        selected = load_local(cache, camera, start, end)
    else:
        # This is the production discover function, pointed at the reference
        # UTC window rather than the current real-time interval.
        kwargs = {} if fetch is None else {"fetch": fetch}
        listed, discovery_errors = hunter.discover(
            camera, end, lookback_hours=(end-start).total_seconds()/3600,
            **kwargs)
        errors.extend(discovery_errors)
        selected = [entry for entry in listed
                    if start <= parse_time(entry["time"]) <= end]
    if len(selected) > max_frames:
        # Never silently take only the last 25 frames: the production loader
        # does this internally and would otherwise hide missing early images.
        return [], errors + [f"FRAME_CAP: {len(selected)} > {max_frames}"]
    acquired = []
    for entry in selected:
        path = trusted_frame_path(cache, entry["name"], camera)
        item = {"name": entry["name"], "time": entry["time"],
                "path": str(path)}
        try:
            if not path.exists():
                if no_download:
                    raise FileNotFoundError(path)
                raw = (hunter.get(entry["url"], limit=MAX_JPEG_BYTES)
                       if fetch is None else fetch(entry["url"], limit=MAX_JPEG_BYTES))
                if not isinstance(raw, bytes):
                    raise ValueError("Nonbinary JPEG response")
                tmp = path.with_name(path.name + ".part")
                try:
                    tmp.write_bytes(raw)
                    validate_jpeg(tmp)
                    tmp.replace(path)
                finally:
                    tmp.unlink(missing_ok=True)
            item.update(validate_jpeg(path))
            item["status"] = "VALIDATED"
            acquired.append(item)
        except Exception as exc:
            item.update(status="ERROR", error=f"{type(exc).__name__}: {exc}")
            errors.append(entry["name"] + ": " + item["error"])
    return acquired, errors


def detector_fingerprint():
    # Fingerprint of the true production routines (not a reimplementation).
    code = "\n".join(inspect.getsource(method)
                     for method in (hunter.load_frames, hunter.peaks,
                                    hunter.track_points, hunter.track_quality))
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def blind_detect(case_name, output_dir, no_download=False, fetch=None):
    if case_name not in CASES:
        raise ValueError("Unknown case")
    case = CASES[case_name]
    output_dir.mkdir(parents=True, exist_ok=True)
    cache = output_dir / case_name / "jpegs"
    manifest, errors = acquire_frames(case, cache, no_download=no_download,
                                      fetch=fetch)
    start, end = parse_time(case["from_utc"]), parse_time(case["through_utc"])
    report = {
        "mode": "POSITION_BLIND_ACTUAL_PRODUCTION_CCOR_JPEG_DETECTOR",
        "case": case_name, "camera": case["camera"],
        "source_level": "NOAA_L3_JPEG", "window_from_utc": case["from_utc"],
        "window_through_utc": case["through_utc"],
        "blind_to_report_positions": True, "blind_to_report_times": False,
        "production_modified": False, "detector_source_sha256": detector_fingerprint(),
        "images": manifest, "errors": errors,
        "frame_count": 0, "track_count": 0, "ranked_tracks": [],
        "reported_comet_recovered": None,
        "reported_comet_score": "NOT_EVALUATED",
        "status": "NO_VALID_IMAGES",
    }
    good = [r for r in manifest if r["status"] == "VALIDATED"]
    if len(good) >= 6:
        # Existing production JPEG resize / 8-bit conversion, unchanged.
        window_h = (end - start).total_seconds()/3600 + 0.01
        frames, times, sizes = hunter.load_frames(cache, case["camera"], end,
                                                   hours=window_h)
        report["frame_count"] = len(frames)
        report["processed_times_utc"] = [hunter.iso(t) for t in times]
        report["original_jpeg_sizes"] = [list(s) for s in sizes]
        report["processing_shape_xy"] = [frames[0].shape[1],frames[0].shape[0]] if frames else None
        report["status"] = "INSUFFICIENT_PROCESSED_FRAMES"
        if len(frames) >= 6:
            # Pass no report locations anywhere into the actual detector.
            tracks = hunter.track_points(frames, times, sizes, case["camera"])
            ranked = sorted(tracks, key=hunter.track_quality, reverse=True)
            report["status"] = "POSITION_BLIND_REPLAY_COMPLETE"
            report["hit_40_track_cap"] = len(ranked) >= 40
            report["track_count"] = len(ranked)
            report["ranked_tracks"] = [
                {**row, "blind_rank": idx+1}
                for idx, row in enumerate(ranked)]
    elif good:
        report["status"] = "INSUFFICIENT_SOURCE_IMAGES"
    dest = output_dir / ("blind_jpeg_" + case_name + ".json")
    dest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(case_name,":",report["status"],"| validated JPEGs",len(good),
          "| processed frames",report["frame_count"],
          "| blind tracks",report["track_count"],flush=True)
    if errors:
        print("  Source issues:",len(errors), "| first:",errors[0],flush=True)
    if report["ranked_tracks"]:
        print("  Top blind candidates:")
        for row in report["ranked_tracks"][:5]:
            print("   ",row["blind_rank"],row["id"],"frames",row["frames"],
                  "RMS",row["rms_px"],"first",row["first"])
    print("  Saved:", dest,flush=True)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--case",choices=tuple(CASES),action="append")
    p.add_argument("--no-download",action="store_true",
                   help="Replay already cached JPEGs; no NOAA network activity")
    args = p.parse_args()
    failures = 0
    for case in dict.fromkeys(args.case or list(CASES)):
        row = blind_detect(case,args.output_dir.expanduser().resolve(),
                           no_download=args.no_download)
        failures += row["status"]!="POSITION_BLIND_REPLAY_COMPLETE"
    if failures:
        raise SystemExit(f"{failures} case(s) incomplete. See saved JSON.")
    print("Position-blind replay finished. No reports read; no hunter changed.")


if __name__=="__main__":
    main()
