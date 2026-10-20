"""Validate CPU first-pass local maxima against simple hand-checked pixels."""
import sys,unittest
from pathlib import Path
try:
 import numpy as np
except ImportError:
 raise unittest.SkipTest("numpy installed by dedicated C3 diagnostic workflow")
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from diagnose_c3_peak_vs_track import moving_box_sum,cuda_first_pass_peaks,spatial_balanced_indices,SIZE

class PeakStages(unittest.TestCase):
 def test_box_sum_shape_and_brightness(self):
  a=np.zeros((SIZE,SIZE),dtype=np.uint8)
  a[80,80]=100
  s=moving_box_sum(a,9)
  self.assertEqual(s.shape,(SIZE,SIZE))
  self.assertEqual(s[80,80],100)
  self.assertEqual(s[100,100],0)
 def test_single_peak_and_no_dark_streak_center(self):
  a=np.full((SIZE,SIZE),120,dtype=np.uint8)
  a[300:450,90:110]=25
  a[360,175]=240
  x,y,strength=cuda_first_pass_peaks(a)
  coords=set(zip(x.tolist(),y.tolist()))
  self.assertIn((175,360),coords)
  self.assertNotIn((100,360),coords)
  self.assertTrue(all(z>=18 for z in strength))
 def test_spatial_balancing_never_adds_peaks(self):
  xs=np.arange(900,dtype=np.int32)%512
  ys=np.arange(900,dtype=np.int32)//512*90+340
  inds=spatial_balanced_indices(xs,ys)
  self.assertEqual(len(inds),700)
  self.assertEqual(len(set(inds.tolist())),700)
if __name__=="__main__":unittest.main()
