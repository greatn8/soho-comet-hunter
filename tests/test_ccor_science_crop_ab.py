"""Offline CCOR crop runner regression tests; synthetic image data only."""
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import ccor_science_crop_ab as crop


class ScienceCropTests(unittest.TestCase):
    def test_crop_shape_and_image_dimensions_guard(self):
        with tempfile.TemporaryDirectory() as d:
            image = Path(d) / "sample.jpg"
            Image.new("L", (1204, 1205), 50).save(image)
            arr = crop.crop_frame(image, (1204, 1205))
            self.assertEqual(arr.shape, (480, 512))
            self.assertEqual(arr.dtype, np.float32)
            self.assertAlmostEqual(float(np.mean(arr)), 50, delta=2)
            with self.assertRaisesRegex(ValueError, "dimensions changed"):
                crop.crop_frame(image, (1024, 960))
            with self.assertRaisesRegex(ValueError, "outside JPEG"):
                crop.crop_frame(image, (1204, 1205), (500, 500, 1024, 960))

    def test_full_coordinates_use_production_width_scale(self):
        # 1204 pixel JPEG is scaled uniformly to 512 pixels by hunter.load_frames.
        factor = 512 / 1204
        source = (1204, 1205)
        inside = {
            "processing_shape": [512, 513],
            "x_first": 95 * factor,
            "y_first": 130 * factor,
            "x_last": 1090 * factor,
            "y_last": 1000 * factor,
        }
        outside = {**inside, "x_first": 20 * factor}
        self.assertTrue(crop.within_crop(inside, source, "full"))
        self.assertFalse(crop.within_crop(outside, source, "full"))

    def test_crop_coordinates_map_into_native_source(self):
        source = (1204, 1205)
        inside = {
            "processing_shape": [512, 480],
            "x_first": 10,
            "y_first": 15,
            "x_last": 500,
            "y_last": 460,
        }
        outside = {**inside, "x_last": 520}
        self.assertTrue(crop.within_crop(inside, source, "crop"))
        self.assertFalse(crop.within_crop(outside, source, "crop"))

    def test_evaluate_runs_both_modes_separately(self):
        def row(ident, points):
            return {
                "id": ident, "frames": points, "rms_px": 0.3,
                "speed_px_h": 4.0, "first": "2026-10-24T00:00:00Z",
                "last": "2026-10-24T01:00:00Z",
                "x_first": 60, "y_first": 70,
                "x_last": 62, "y_last": 71, "processing_shape": [512, 513],
            }
        old, extra = row("old", 4), row("new", 6)
        calls = []

        def fake_track(frames, times, sizes, camera, **kwargs):
            calls.append(kwargs)
            info = {"scan_completed": True, "distinct_hypotheses": 2,
                    "stop_reason": None}
            return ([old, extra] if kwargs["complete_search"] else [old], info)

        with patch.object(crop.hunter, "track_points", side_effect=fake_track):
            result = crop.evaluate([np.zeros((1, 1))], [], [(1204, 1205)], "ccor1", "full")
        self.assertEqual(result["legacy_track_count"], 1)
        self.assertEqual(result["complete_top40_track_count"], 2)
        self.assertEqual(result["new_hypothesis_ids_in_complete_top40"], 1)
        self.assertEqual([c["complete_search"] for c in calls], [False, True])

    def test_incomplete_budget_does_not_claim_new_top40(self):
        def fake_track(*args, **kwargs):
            return ([], {"scan_completed": not kwargs["complete_search"],
                         "distinct_hypotheses": 5000})
        with patch.object(crop.hunter, "track_points", side_effect=fake_track):
            result = crop.evaluate([np.zeros((1, 1))], [], [(1204, 1205)], "ccor2", "crop")
        self.assertEqual(result["complete_search_status"], "INCOMPLETE_BUDGET_EXCEEDED")
        self.assertIsNone(result["new_hypothesis_ids_in_complete_top40"])


if __name__ == "__main__":
    unittest.main()
