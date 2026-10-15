"""Regression tests for realtime track chronology and review availability."""
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from realtime_hunter import parse_candidates
from live_review import ensure_live_review


def detector_output(first, last):
    return (
        "C24 [MEDIUM] frames=5 speed=2.30 px/h vx=0.95 vy=2.09 "
        "RMS=0.31 brightnessCV=0.25 sunward=1.73 px/h score=75.5\n"
        f" first: {first} (100.0, 100.0)\n"
        f" last: {last} (101.0, 102.0)\n"
    )


class RealtimeIntegrityTests(unittest.TestCase):
    def test_backwards_endpoints_are_ordered_and_flagged(self):
        rows = parse_candidates(detector_output(
            "20261008_0442_c3_512.jpg", "20261005_2218_c3_512.jpg"
        ))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row["endpoint_order_corrected"])
        self.assertLess(row["first"]["time"], row["last"]["time"])
        self.assertEqual(row["trajectory_warning"], "ENDPOINT_VELOCITY_MISMATCH")

    def test_identical_endpoints_are_not_assumed_valid(self):
        rows = parse_candidates(detector_output(
            "20261006_1242_c3_512.jpg", "20261006_1242_c3_512.jpg"
        ))
        self.assertEqual(rows[0]["trajectory_warning"], "ENDPOINTS_SAME_TIMESTAMP")

    def test_review_does_not_make_video_for_zero_span(self):
        t = dt.datetime(2026, 10, 6, 12, 42, tzinfo=dt.timezone.utc)
        candidate = {
            "cid": "C06", "vx": 1.0, "vy": 1.0,
            "first": {"time": t, "file": "20261006_1242_c3_512.jpg",
                      "x": 100.0, "y": 100.0},
            "last": {"time": t, "file": "20261006_1242_c3_512.jpg",
                     "x": 101.0, "y": 102.0},
        }
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            with self.assertRaisesRegex(RuntimeError, "positive time span"):
                ensure_live_review(candidate, base / "cache", base / "results")


if __name__ == "__main__":
    unittest.main()
