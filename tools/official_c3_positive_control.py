"""Official, independently reported SOHO C3 engineering positive control.

Sungrazer report_wb_20260728191608 (Worachate Boonplod): "Known Comet
Now Entering", Kreutz, LASCO C3, observations on 2026-07-28 UTC.
The reporting interface uses 1024px coordinates with origin at the lower
left; the CUDA JPEG detector uses top-left image coordinates.

These observations support a regression/sanity check, NOT a scientific
claim of confirmation or a link to the separately dated July 30 event.

References:
https://sungrazer.nrl.navy.mil/reports?order=title&page=93&sort=desc
https://sungrazer.nrl.navy.mil/soho-discoveries
"""
from datetime import datetime, timezone

EVENT_ID = "OFFICIAL_C3_20260728_WB"
REPORT_ID = "report_wb_20260728191608"

# UTC time and (x, y) exactly as in the report, lower-left origin at 1024.
ORIGINAL_POINTS = (
    ("2026-07-28T21:54:00Z", 839.0, 790.0),
    ("2026-07-28T22:06:00Z", 838.0, 789.0),
    ("2026-07-28T22:18:00Z", 837.0, 788.0),
)


def _utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def control():
    return {
        "event_id": EVENT_ID,
        "known_object": "Known entering Kreutz (official C3 report, 2026-07-28)",
        "first": _utc(ORIGINAL_POINTS[0][0]),
        "last": _utc(ORIGINAL_POINTS[-1][0]),
        "historical_frames": len(ORIGINAL_POINTS),
        "report_id": REPORT_ID,
    }


def truth_points():
    # Bottom-left to image top-left. Report values were measured at 1024.
    return [(_utc(t), x, 1024.0-y) for t,x,y in ORIGINAL_POINTS]
