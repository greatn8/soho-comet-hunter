#!/usr/bin/env python3
"""Conservative CCOR track motion-consistency triage.

Coordinates are detector pixels, not calibrated sky astrometry. These are
review-priority heuristics, NOT a physical comet classification. Abrupt
three-observation kinks are suspicious; smooth curvature remains eligible.
Time spacing matters because NOAA frames can be missing. The input is never
changed and the full hypothesis stays available for diagnostics.
"""
from __future__ import annotations

import datetime as dt
import math

# All values are in the detector's approximately 512-pixel image plane.
# A one-pixel centroid quantization error can cause large instantaneous
# velocity variations across 15-minute frames, so require BOTH conditions.
KINK_LIMIT_PX = 3.0
VELOCITY_CHANGE_LIMIT_PX_H = 12.0
REVERSE_MIN_SEGMENT_PX = 2.0
REVERSE_COSINE_LIMIT = -0.5


def motion_consistency(track):
    """Return descriptive evidence without modifying or rejecting a track."""
    measurements = []
    for point in track.get("points") or ():
        try:
            stamp = dt.datetime.fromisoformat(str(point["time"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("Timestamp lacks UTC offset")
            x, y = float(point["x"]), float(point["y"])
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError("Non-finite pixel")
            measurements.append((stamp, x, y))
        except (KeyError, TypeError, ValueError, OverflowError):
            return {"status": "UNASSESSABLE", "reason": "Invalid time or detector coordinates",
                    "triples_checked": 0, "max_kink_px": None,
                    "max_velocity_change_px_h": None, "direction_reversals": 0}
    measurements.sort(key=lambda row: row[0])
    if len(measurements) < 4:
        return {"status": "UNASSESSABLE", "reason": "Fewer than four measured frames",
                "triples_checked": 0, "max_kink_px": None,
                "max_velocity_change_px_h": None, "direction_reversals": 0}

    max_kink = 0.0
    max_velocity_change = 0.0
    reversals = 0
    unusual_kinks = 0
    checked = 0
    for index in range(1, len(measurements) - 1):
        first, middle, last = (measurements[index-1],
                               measurements[index], measurements[index+1])
        first_hours = (middle[0] - first[0]).total_seconds() / 3600.0
        second_hours = (last[0] - middle[0]).total_seconds() / 3600.0
        if first_hours <= 0 or second_hours <= 0:
            return {"status": "UNASSESSABLE",
                    "reason": "Non-increasing observation times",
                    "triples_checked": checked,
                    "max_kink_px": None,
                    "max_velocity_change_px_h": None,
                    "direction_reversals": reversals}
        fraction = first_hours / (first_hours + second_hours)
        # Distance from the time-interpolated position between outer frames.
        ex = first[1] + fraction * (last[1] - first[1])
        ey = first[2] + fraction * (last[2] - first[2])
        kink = math.hypot(middle[1] - ex, middle[2] - ey)
        dx1, dy1 = middle[1] - first[1], middle[2] - first[2]
        dx2, dy2 = last[1] - middle[1], last[2] - middle[2]
        velocity_change = math.hypot(
            dx2 / second_hours - dx1 / first_hours,
            dy2 / second_hours - dy1 / first_hours,
        )
        max_kink = max(max_kink, kink)
        max_velocity_change = max(max_velocity_change, velocity_change)
        if kink > KINK_LIMIT_PX and velocity_change > VELOCITY_CHANGE_LIMIT_PX_H:
            unusual_kinks += 1

        mag1, mag2 = math.hypot(dx1, dy1), math.hypot(dx2, dy2)
        if mag1 >= REVERSE_MIN_SEGMENT_PX and mag2 >= REVERSE_MIN_SEGMENT_PX:
            cosine = (dx1 * dx2 + dy1 * dy2) / (mag1 * mag2)
            if cosine < REVERSE_COSINE_LIMIT:
                reversals += 1
        checked += 1

    is_irregular = unusual_kinks > 0 or reversals > 0
    return {
        "status": "IRREGULAR_MOTION" if is_irregular else "PROVISIONALLY_CONSISTENT",
        "reason": ("Abrupt local speed/direction change; inspect original frames"
                   if is_irregular else "No large local kinks detected"),
        "triples_checked": checked,
        "max_kink_px": round(max_kink, 2),
        "max_velocity_change_px_h": round(max_velocity_change, 2),
        "large_kinks": unusual_kinks, "direction_reversals": reversals,
        "thresholds": {"kink_px": KINK_LIMIT_PX,
                       "velocity_change_px_h": VELOCITY_CHANGE_LIMIT_PX_H},
    }


def annotated_track(track):
    """Return a separate annotated copy; do not overwrite raw input evidence."""
    result = dict(track)
    result["motion_consistency"] = motion_consistency(track)
    return result


def irregular(track):
    """Only definite heuristic flags are deprioritized; unknowns stay visible."""
    return motion_consistency(track)["status"] == "IRREGULAR_MOTION"
