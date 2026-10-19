"""Read-only C3 JPEG reference pixel audit unit tests."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from audit_reported_comet_pixels import mapped_point, local_residual, SIZE, audit_one

class PixelAuditTests(unittest.TestCase):
    def test_coordinate_origin_variants(self):
        self.assertEqual(mapped_point(670,910,"upper_left"),(670,910))
        self.assertEqual(mapped_point(670,910,"lower_left"),(670,113))
        self.assertEqual(mapped_point(670,910,"horizontal_flip"),(353,910))
        self.assertEqual(mapped_point(670,910,"rotate_180"),(353,113))

    def test_identifiable_local_peak(self):
        image=bytearray([10])*(SIZE*SIZE)
        x,y=670,910
        image[y*SIZE+x]=250
        self.assertGreater(local_residual(image,x,y),200)
        result=audit_one(image,[image],x,y)
        self.assertEqual(result["peaks_above_threshold_18"],1)
        self.assertEqual(result["best_local_maximum"]["x"],x)
        self.assertEqual(result["best_local_maximum"]["temporal_novelty"],0)

if __name__=="__main__":unittest.main()
