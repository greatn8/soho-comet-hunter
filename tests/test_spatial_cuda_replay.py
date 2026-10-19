"""Offline spatial CUDA replay geometry regression tests."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from benchmark_spatial_cuda import region_bounds,mask_filter,summarize_tracks

class SpatialReplayTests(unittest.TestCase):
    def test_overlapping_regions_preserve_original_coordinates(self):
        self.assertEqual(region_bounds(1024,4,0,48),(0,0,304,304))
        self.assertEqual(region_bounds(1024,4,15,48),(720,720,1024,1024))
        self.assertEqual(region_bounds(1024,4,6,48),(464,208,816,560))
    def test_mask_filter_blacks_only_outside(self):
        vf=mask_filter(1024,(464,208,816,560))
        self.assertIn("drawbox=x=0:y=0:w=1024:h=208",vf)
        self.assertIn("drawbox=x=0:y=208:w=464:h=352",vf)
    def test_no_track_is_verified_just_for_roi_membership(self):
        result=summarize_tracks([],(0,0,256,256),23,250)
        self.assertEqual(result["midpoints_inside_roi"],0)
        self.assertEqual(result["good_fit_rms_le_1_2"],0)
        self.assertFalse(result["cap_reached"])

if __name__=="__main__":unittest.main()
