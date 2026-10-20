"""Host unit tests for independent comet pixel-stage diagnostics."""
from pathlib import Path
import sys
import unittest

try:
    import numpy as np
except ImportError:
    raise unittest.SkipTest("CPU pixel diagnostic requires numpy; the dedicated workflow installs it")

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from diagnose_official_c3_prelink import raw_maxima,truth_rank,sample_mask_reason

class PrelinkTests(unittest.TestCase):
    def test_bright_comet_peak_is_passed_to_cuda(self):
        im=np.zeros((128,128),dtype=np.uint8)
        im[50,90]=100
        peaks=raw_maxima(im)
        result=truth_rank(peaks,(90,50),0)
        self.assertEqual(result["reason"],"IN_CUDA_POINT_LIST")
        self.assertEqual(result["peak_rank"],1)
        self.assertAlmostEqual(result["best_residual"],100)

    def test_faint_comet_peak_lost_to_threshold(self):
        im=np.zeros((128,128),dtype=np.uint8)
        im[50,90]=10
        peaks=raw_maxima(im)
        self.assertEqual(truth_rank(peaks,(90,50),0)["reason"],
                         "BELOW_RESIDUAL_THRESHOLD")
        self.assertEqual(truth_rank(peaks,(90,50),15)["reason"],
                         "IN_CUDA_POINT_LIST")

    def test_solar_core_and_outer_edges_are_explicit(self):
        self.assertEqual(sample_mask_reason(10,60,128,128),"MASKED_IMAGE_BORDER")
        self.assertEqual(sample_mask_reason(64,64,128,128),"MASKED_SOLAR_CENTRE")
        self.assertIsNone(sample_mask_reason(90,50,128,128))

if __name__=="__main__":
    unittest.main()
