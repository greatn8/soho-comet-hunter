#!/usr/bin/env python3
"""Replay the first C3 CUDA search against the 8 Nov 2026 Kreutz report.

Run on Bourbaki against cached 1024 JPEGs. Uses a temporary project and
historical frame cutoffs. No network requests, notifications or live writes.
"""
import argparse
from copy import deepcopy
from datetime import timedelta
import json
import math
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

import realtime_hunter as hunter


REPORT_FRAME = "20261108_1330"
REPORT_TIME = hunter.image_time(REPORT_FRAME)
# Linear fit to the seven top-left-origin report positions. Before 13:30
# this is an extrapolated locator, so compatible tracks require visual review.
REPORT_XY = (591.18, 652.07)
REPORT_VELOCITY = (-6.61, -9.25)


def reference_distance(track):
    first, last = track["first"]["time"], track["last"]["time"]
    start = max(first, REPORT_TIME - timedelta(hours=3))
    end = min(last, REPORT_TIME + timedelta(minutes=84))
    if end - start < timedelta(minutes=36):
        return math.inf
    errors = []
    for when in (start, start + (end - start) / 2, end):
        hours = (when - REPORT_TIME).total_seconds() / 3600
        expected = tuple(x + v * hours for x, v in zip(REPORT_XY, REPORT_VELOCITY))
        errors.append(math.dist(hunter.position_at(track, when, model="endpoints"), expected))
    return max(errors)


def review(rows, frames, folder, args, cutoff, control_minutes):
    original = hunter.verify_temporal_motion

    def motion(*positional, **kwargs):
        kwargs["earlier_control_minutes"] = control_minutes
        return original(*positional, **kwargs)

    with patch.object(hunter, "verify_temporal_motion", motion):
        data = hunter.process_early_tracks(
            deepcopy(rows), frames, folder, args, cutoff, hunter,
            notify_enabled=False, report_fetch=lambda: "",
            now=hunter.image_time(cutoff),
        )
    return {row["candidate"]: row for row in data["candidates"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--cutoff", action="append", metavar="YYYYMMDD_HHMM")
    parser.add_argument("--max-candidates", type=int, default=100)
    parser.add_argument("--short-budget-minutes", type=int, default=2)
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
        for flag in ("realtime_cuda_stationary_enabled", "realtime_cuda_enhanced_enabled"):
            if (project / "state" / flag).is_file():
                (root / "state" / flag).touch()
        for cutoff in cutoffs:
            inputs = [p for p in frames if p.name[:13] <= cutoff]
            if not hunter.wait_for_gpu(project, 30):
                raise SystemExit("Live CUDA is busy. Try again when a detection pass has finished.")
            started = time.monotonic()
            raw, runs = hunter.run_multiscale_cuda(root, inputs, root, args, cutoff, root)
            search_seconds = time.monotonic() - started
            compatible = [(track, reference_distance(track)) for track in raw]
            compatible = [(track, error) for track, error in compatible if error <= 8]
            baseline = review(raw, inputs, root / "baseline", args, cutoff, 0) if compatible else {}
            patched = review(raw, inputs, root / "patched", args, cutoff, 90) if compatible else {}
            matches = []
            for track, error in sorted(compatible, key=lambda item: item[1]):
                identifier = track["cid"]
                item = {"first": track["first"]["file"], "last": track["last"]["file"],
                        "frames": track["frames"], "max_reference_error_px": round(error, 2)}
                for name, reviews in (("baseline", baseline), ("patched", patched)):
                    row = reviews.get(identifier)
                    if row is None:
                        item[name] = {"stage": "BEFORE_STRONG_PIXEL_REVIEW"}
                    else:
                        reasons = [reason for reason in row["early_alert_reasons"]
                                   if reason != "REPORT_CHECK_UNAVAILABLE_OR_MATCHED"]
                        item[name] = {"image_gates_pass": not reasons, "reasons": reasons,
                                      "motion": row["pixel_verification"]["temporal_motion"]}
                matches.append(item)
            print(json.dumps({"cutoff_utc": cutoff, "search_seconds": round(search_seconds, 1),
                              "search": runs[0], "reference_compatible_tracks": matches,
                              "report_gate": "NOT_SIMULATED"}), flush=True)


if __name__ == "__main__":
    main()
