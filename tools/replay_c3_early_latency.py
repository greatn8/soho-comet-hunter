#!/usr/bin/env python3
"""Replay the first C3 CUDA search against the 8 Nov 2026 Kreutz report.

Run on Bourbaki against cached 1024 JPEGs. Uses a temporary project and
historical frame cutoffs. No network requests, notifications or live writes.
"""
import argparse
from contextlib import nullcontext
from copy import deepcopy
from datetime import timedelta
import json
import math
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

import realtime_hunter as hunter
import early_c3_review


REPORT_FRAME = "20261108_1330"
REPORT_TIME = hunter.image_time(REPORT_FRAME)
# Linear fit to the seven top-left-origin report positions. Before 13:30
# this is an extrapolated locator, so compatible tracks require visual review.
REPORT_XY = (591.18, 652.07)
REPORT_VELOCITY = (-6.61, -9.25)
REPORT_POINTS = [
    (hunter.image_time("20261108_" + stamp), x, y)
    for stamp, x, y in [
        ("1330", 591, 652), ("1342", 590, 650), ("1354", 589, 648),
        ("1406", 587, 647), ("1430", 585, 644), ("1442", 583, 640),
        ("1454", 582, 639),
    ]
]


DASHBOARD_TIME = hunter.image_time("20261108_0942")
DASHBOARD_XY = (638.0, 674.0)
DASHBOARD_VELOCITY = (-7.88, -8.72)


def reference_distance(track, reference="report"):
    first, last = track["first"]["time"], track["last"]["time"]
    start = max(first, REPORT_TIME - timedelta(hours=3))
    end = min(last, REPORT_TIME + timedelta(minutes=84))
    if end - start < timedelta(minutes=36):
        return math.inf
    if reference == "dashboard":
        anchor, xy, velocity = DASHBOARD_TIME, DASHBOARD_XY, DASHBOARD_VELOCITY
    else:
        anchor, xy, velocity = REPORT_TIME, REPORT_XY, REPORT_VELOCITY
    errors = []
    for when in (start, start + (end - start) / 2, end):
        hours = (when - anchor).total_seconds() / 3600
        expected = tuple(x + v * hours for x, v in zip(xy, velocity))
        errors.append(math.dist(hunter.position_at(track, when, model="endpoints"), expected))
    return max(errors)


def review(rows, frames, folder, args, cutoff, control_minutes):
    original = hunter.verify_temporal_motion

    def motion(*positional, **kwargs):
        kwargs["earlier_control_minutes"] = control_minutes
        return original(*positional, **kwargs)

    continuation = (nullcontext() if control_minutes else
                    patch.object(early_c3_review, "verify_recent_continuation", return_value=None))
    with patch.object(hunter, "verify_temporal_motion", motion), continuation:
        data = hunter.process_early_tracks(
            deepcopy(rows), frames, folder, args, cutoff, hunter,
            notify_enabled=False, report_fetch=lambda: "",
            now=hunter.image_time(cutoff),
        )
    return {row["candidate"]: row for row in data["candidates"]}


def summarize_track(track, reference_error):
    first, last = track["first"], track["last"]
    reported_errors = [
        math.dist(hunter.position_at(track, when, model="endpoints"), (x, y))
        for when, x, y in REPORT_POINTS if first["time"] <= when <= last["time"]
    ]
    return {
        "first": first["file"], "first_xy": [first["x"], first["y"]],
        "last": last["file"], "last_xy": [last["x"], last["y"]],
        "frames": track["frames"],
        "max_reference_error_px": round(reference_error, 2),
        "reported_points_inside_track": len(reported_errors),
        "max_measured_report_error_px": round(max(reported_errors), 2) if reported_errors else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--cutoff", action="append", metavar="YYYYMMDD_HHMM")
    parser.add_argument("--reference", choices=("report", "dashboard"), default="report",
                        help="Match the published positions or the C9000000297171 dashboard path")
    parser.add_argument("--max-candidates", type=int, default=100)
    parser.add_argument("--short-budget-minutes", type=int, default=2)
    parser.add_argument("--disable-stationary", action="store_true",
                        help="Disable the stationary filter in this replay only")
    parser.add_argument("--disable-enhanced", action="store_true",
                        help="Disable the enhanced spatial, integrity and null filters in this replay only")
    args = parser.parse_args()
    args.resolution = 1024
    args.search_windows = (12,)
    args.min_frames = 5
    args.medium_budget_minutes = args.compute_minutes = args.short_budget_minutes
    project, cache = args.project.expanduser().resolve(), args.cache.expanduser().resolve()
    binary = project / "comet_hunter_archive"
    if not binary.is_file():
        parser.error(f"CUDA binary missing: {binary}")
    frames = sorted(p for p in cache.glob("*_c3_1024.jpg") if hunter.IMAGE_RE.fullmatch(p.name))
    cutoffs = sorted(set(args.cutoff or ["20261108_1154", "20261108_1218",
                                         "20261108_1230", REPORT_FRAME]))
    frame_names = {p.name[:13] for p in frames}
    missing = [cutoff for cutoff in cutoffs if cutoff not in frame_names]
    if missing:
        parser.error(f"Original cutoff frames missing in {cache}: {', '.join(missing)}")
    hunter.pixel_verify.configure_resolution(1024)

    with tempfile.TemporaryDirectory(prefix="c3-early-replay-") as tmp:
        root = Path(tmp)
        (root / "comet_hunter_archive").symlink_to(binary)
        (root / "state").mkdir()
        for flag, disabled in (
            ("realtime_cuda_stationary_enabled", args.disable_stationary),
            ("realtime_cuda_enhanced_enabled", args.disable_enhanced),
        ):
            if not disabled and (project / "state" / flag).is_file():
                (root / "state" / flag).touch()
        for cutoff in cutoffs:
            inputs = [p for p in frames if p.name[:13] <= cutoff]
            if not hunter.wait_for_gpu(project, 30):
                raise SystemExit("Live CUDA is busy. Try again when a detection pass has finished.")
            started = time.monotonic()
            raw, runs = hunter.run_multiscale_cuda(root, inputs, root, args, cutoff, root)
            search_seconds = time.monotonic() - started
            distances = [(track, reference_distance(track, args.reference)) for track in raw]
            distances = sorted(
                ((track, error) for track, error in distances if math.isfinite(error)),
                key=lambda item: item[1],
            )
            compatible = [(track, error) for track, error in distances if error <= 8]
            nearest = [summarize_track(track, error) for track, error in distances[:5]]
            baseline = review(raw, inputs, root / "baseline", args, cutoff, 0) if compatible else {}
            patched = review(raw, inputs, root / "patched", args, cutoff, 90) if compatible else {}
            matches = []
            for track, error in compatible:
                identifier = track["cid"]
                item = summarize_track(track, error)
                for name, reviews in (("baseline", baseline), ("patched", patched)):
                    row = reviews.get(identifier)
                    if row is None:
                        item[name] = {"stage": "BEFORE_STRONG_PIXEL_REVIEW"}
                    else:
                        reasons = [reason for reason in row["early_alert_reasons"]
                                   if reason != "REPORT_CHECK_UNAVAILABLE_OR_MATCHED"]
                        item[name] = {"image_gates_pass": not reasons, "reasons": reasons,
                                      "last_verified_frame": row["last"],
                                      "motion": row["pixel_verification"]["temporal_motion"]}
                matches.append(item)
            print(json.dumps({"cutoff_utc": cutoff, "search_seconds": round(search_seconds, 1),
                              "reference": args.reference,
                              "search": runs[0], "reference_compatible_tracks": matches,
                              "nearest_reference_tracks": nearest,
                              "report_gate": "NOT_SIMULATED"}), flush=True)


if __name__ == "__main__":
    main()
