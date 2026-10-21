"""CPU-only 512/1024 regression checks for SOHO comet pipeline."""
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import pixel_verify
import realtime_hunter
import comet_github_alert
import extract_submission_frames


class ResolutionTests(unittest.TestCase):
    def tearDown(self):
        pixel_verify.configure_resolution(512)

    def test_native_parser(self):
        for size in (512, 1024):
            filename = f"20261021_0930_c3_{size}.jpg"
            self.assertEqual(
                realtime_hunter.explicit_images(
                    f'<a href="{filename}">{filename}</a>', resolution=size),
                [filename])
            self.assertEqual(comet_github_alert.utc_of_frame(filename).hour, 9)
            self.assertEqual(extract_submission_frames.image_time(filename).minute, 30)

    def test_sample_size(self):
        for size in (512, 1024):
            pixel_verify.configure_resolution(size)
            self.assertEqual(pixel_verify.PIXELS, size * size)
            gray = bytes([100]) * (size * size)
            m = pixel_verify._sample_signal(gray, size // 2, size // 2)
            self.assertIsNotNone(m)
            self.assertAlmostEqual(m["contrast"], 0.0)
            self.assertIsNone(pixel_verify._sample_signal(gray, 0, 0))

    def test_same_scale_sungrazer_projection(self):
        when = dt.datetime(2026, 10, 21, 9, 30, tzinfo=dt.timezone.utc)
        for size in (512, 1024):
            scale = size / 512.0
            c = {"first":{"x":100*scale,"y":150*scale,"time":when},
                 "last":{"x":101*scale,"y":151*scale,"time":when+dt.timedelta(hours=1)},
                 "vx":2*scale,"vy":3*scale,"resolution":size}
            x,y = realtime_hunter.candidate_xy_1024(c,when)
            self.assertAlmostEqual(x,200)
            self.assertAlmostEqual(y,300)

    def test_alert_endpoint_bounds(self):
        for size in (512,1024):
            c = {"first":f"20261021_0930_c3_{size}.jpg",
                 "last":f"20261021_0954_c3_{size}.jpg",
                 "resolution":size,
                 "track":{"first_x":size-10,"first_y":size-20,
                          "last_x":size-9,"last_y":size-19}}
            rows = comet_github_alert.report_position_rows(c)
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]["x"],size-10)
            _,body = comet_github_alert.issue_details(c)
            self.assertIn(f"{size}x{size}",body)

if __name__ == "__main__":
    unittest.main()
