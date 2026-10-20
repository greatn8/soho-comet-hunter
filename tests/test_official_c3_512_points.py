"""Source-level controls for 512px known-comet first-pass audit."""
import sys,unittest
from pathlib import Path
try:
 import numpy as np
except ImportError:
 raise unittest.SkipTest("numpy required in dedicated diagnostic CI")
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from audit_official_c3_512_points import near_peak,point_diagnostic,tally,level_settings,in_cuda_field
class TruthPeakTests(unittest.TestCase):
 def test_matching_and_negative_position(self):
  x=np.array([100,200]);y=np.array([350,360])
  self.assertTrue(near_peak(x,y,101,350)["hit"])
  self.assertFalse(near_peak(x,y,115,350)["hit"])
 def test_field_edges_and_sun_block(self):
  self.assertFalse(in_cuda_field(12,200))
  self.assertFalse(in_cuda_field(255,255))
  self.assertTrue(in_cuda_field(100,350))
 def test_native_threshold_and_caps(self):
  self.assertEqual(level_settings(0)["threshold"],18)
  self.assertEqual(level_settings(0)["max_points"],700)
  self.assertEqual(level_settings(11)["max_points"],4000)
 def test_known_bright_peak_survives(self):
  a=np.full((512,512),100,dtype=np.uint8)
  a[350,100]=235
  report=point_diagnostic(a,100,350,0)
  for stage in ("raw_thresholded_peaks","global_brightest","spatial_balanced"):
   self.assertTrue(report["actual"][stage]["hit"])
  self.assertFalse(report["shifted_controls"]["raw_thresholded_peaks"][0])
 def test_dark_object_not_bright_peak(self):
  a=np.full((512,512),100,dtype=np.uint8)
  a[350,100]=0
  report=point_diagnostic(a,100,350,0)
  self.assertFalse(report["actual"]["raw_thresholded_peaks"]["hit"])
if __name__=="__main__":unittest.main()
