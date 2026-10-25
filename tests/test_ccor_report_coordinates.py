"""Reporting coordinate scale sanity checks without claiming sky calibration."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from ccor_report_coordinates import image_to_native,native_to_image,prepare_reporting_coordinates

class Coordinates(unittest.TestCase):
    def test_both_axes_resize_without_square_cropping(self):
        x,y=image_to_native(380,300,[512,480],[1024,960])
        self.assertAlmostEqual(x,1521.5)
        self.assertAlmostEqual(y,1201.5)
        xx,yy=native_to_image(x,y,[512,480],[1024,960])
        self.assertAlmostEqual(xx,380)
        self.assertAlmostEqual(yy,300)
    def test_no_unverified_reporting_claims(self):
        t={"camera":"ccor2","id":"case","processing_shape":[512,480],
           "source_jpeg_shape":[1024,960],
           "points":[{"time":"2026-10-22T23:45:00Z","x":380,"y":300}]}
        r=prepare_reporting_coordinates(t)
        self.assertFalse(r["report_ready"])
        self.assertEqual(r["coordinate_kind"],"CCD_IMAGE_PIXELS_NOT_CELESTIAL")
        self.assertEqual(len(r["observations"]),1)
    def test_rejects_unexpected_image_size(self):
        with self.assertRaises(ValueError):
            image_to_native(10,20,[512,512],[800,800])

if __name__=="__main__":unittest.main()
