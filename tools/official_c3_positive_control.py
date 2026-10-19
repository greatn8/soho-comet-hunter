"""Official, independently reported SOHO C3 engineering positive control.

Sungrazer report_wb_20260728191608 (Worachate Boonplod): "Known Comet
Now Entering", Kreutz, LASCO C3, observations on 2026-07-28 UTC.
The report lists 1024px coordinates, but the scraped listing does not specify
which vertical coordinate origin was used. Both upper-left and lower-left
interpretations are tested until the reporting convention is independently
confirmed.

These observations support a regression/sanity check, NOT a scientific
claim of confirmation or a link to the separately dated July 30 event.

References:
https://sungrazer.nrl.navy.mil/reports?order=title&page=93&sort=desc
https://sungrazer.nrl.navy.mil/soho-discoveries
"""
from datetime import datetime, timezone

EVENT_ID = "OFFICIAL_C3_20260728_WB"
REPORT_ID = "report_wb_20260728191608"

# UTC time and (x, y) exactly as displayed in the 1024px report.
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


def truth_points(origin="lower_left"):
    if origin not in ("lower_left", "upper_left"):
        raise ValueError("origin must be lower_left or upper_left")
    return [(_utc(t), x, 1024.0-y if origin=="lower_left" else y)
            for t,x,y in ORIGINAL_POINTS]
