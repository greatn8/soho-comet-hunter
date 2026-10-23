"""Synthetic source controls for isolated native CCOR FITS residual audit."""
import sys,unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import ccor_fits_temporal_controls as c
class TestCCORControls(unittest.TestCase):
 def test_geometry_reflections(self):
  self.assertEqual(c.xy_for_shape(4,7,(100,200),"raw_upper_left"),(4,7))
  self.assertEqual(c.xy_for_shape(4,7,(100,200),"raw_lower_left"),(4,92))
  self.assertEqual(c.xy_for_shape(4,7,(100,200),"flipped_x_lower_left"),(195,92))
 def test_stationary_source_subtracted(self):
  rng=np.random.default_rng(42)
  frames=[rng.normal(100,1,(120,120)).astype("float32") for _ in range(7)]
  for a in frames:a[60,60]+=60
  r=c.residual_measurement(frames,3,(60,60))
  self.assertEqual(r["status"],"MEASURED")
  self.assertLess(r["center_snr"],3)
 def test_moving_source_survives(self):
  rng=np.random.default_rng(22)
  frames=[rng.normal(100,1,(120,120)).astype("float32") for _ in range(7)]
  for i,a in enumerate(frames):
   a[59:62,39+i*6:42+i*6]+=70
  r=c.residual_measurement(frames,3,(58,60))
  self.assertEqual(r["status"],"MEASURED")
  self.assertGreater(r["peak_snr"],5)
 def test_out_of_bounds(self):
  a=[np.ones((100,100),dtype="float32") for _ in range(5)]
  self.assertIsNone(c.residual_measurement(a,2,(5,5)))
if __name__=="__main__":unittest.main()
