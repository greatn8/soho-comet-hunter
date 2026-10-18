"""Independent checks of CUDA candidate time spans and geometric paths.

A CUDA score or reported frame count is not independent observation evidence.
This module never deletes raw detections; it records what is actually supported
by the source image sequence.
"""
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path


def audit_track_frames(candidate, source_frames):
    """Annotate whether the stated number of frames could fit the source span."""
    first = candidate["first"]["time"]
    last = candidate["last"]["time"]
    frames = int(candidate.get("frames", 0))
    available = 0
    for file in source_frames:
        try:
            timestamp = dt.datetime.strptime(
                Path(file).name[:13], "%Y%m%d_%H%M"
            ).replace(tzinfo=dt.timezone.utc)
        except (ValueError, TypeError):
            continue
        if first <= timestamp <= last:
            available += 1
    issues = list(candidate.get("quality_issues") or [])
    warning = candidate.get("trajectory_warning")
    if warning and warning not in issues:
        issues.append(warning)
    if last <= first and "ENDPOINTS_SAME_TIMESTAMP" not in issues:
        issues.append("ENDPOINTS_SAME_TIMESTAMP")
    if frames > available and "FRAME_COUNT_EXCEEDS_SOURCE_IMAGES" not in issues:
        issues.append("FRAME_COUNT_EXCEEDS_SOURCE_IMAGES")
    if available < 2 and "INSUFFICIENT_SOURCE_IMAGES" not in issues:
        issues.append("INSUFFICIENT_SOURCE_IMAGES")
    candidate["available_source_frames"] = available
    candidate["quality_issues"] = issues
    candidate["trajectory_warning"] = issues[0] if issues else ""
    return candidate


def position_at(candidate, when, model="velocity"):
    first = candidate["first"]
    last = candidate["last"]
    span = (last["time"] - first["time"]).total_seconds()
    if model == "endpoints":
        if span <= 0:
            return float("nan"), float("nan")
        fraction = (when - first["time"]).total_seconds() / span
        return (
            float(first["x"]) + fraction * (float(last["x"]) - float(first["x"])),
            float(first["y"]) + fraction * (float(last["y"]) - float(first["y"])),
        )
    if model != "velocity":
        raise ValueError(f"Unknown trajectory model: {model}")
    elapsed = (when - first["time"]).total_seconds() / 3600.0
    return (
        float(first["x"]) + float(candidate["vx"]) * elapsed,
        float(first["y"]) + float(candidate["vy"]) * elapsed,
    )


def source_track_consistent(candidate):
    """Separate track integrity from low pixel brightness.

    Bad fits remain available for review but can never be automatically
    escalated to a confirmed discovery.
    """
    disallowed = {
        "ENDPOINTS_SAME_TIMESTAMP",
        "FRAME_COUNT_EXCEEDS_SOURCE_IMAGES",
        "INSUFFICIENT_SOURCE_IMAGES",
    }
    return not any(item in disallowed for item in candidate.get("quality_issues", []))


def endpoint_velocity_error(candidate):
    first = candidate["first"]
    last = candidate["last"]
    hours = (last["time"] - first["time"]).total_seconds() / 3600.0
    if hours <= 0:
        return math.inf
    dx = float(last["x"]) - float(first["x"])
    dy = float(last["y"]) - float(first["y"])
    return math.hypot(dx - float(candidate["vx"]) * hours,
                      dy - float(candidate["vy"]) * hours)
