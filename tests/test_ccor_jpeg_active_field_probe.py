"""Offline JPEG active-field rectangle diagnostics."""
import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import ccor_jpeg_active_field_probe as probe

class FieldProbeTests(unittest.TestCase):
 def test_rank_rectangle_on_known_activity(self):
  a=np.zeros((15,17),dtype=np.float32)
  a[3:11,5:14]=10
  top=probe.rank_regions(a,region_xy=(9,8),stride=1,topn=1)[0]
  self.assertEqual((top["left"],top["top"]),(5,3))
  self.assertGreater(top["inside_to_outside_activity_ratio"],1000)
 def test_temporal_change(self):
  a=np.zeros((8,9),dtype=np.uint8)
  b=np.zeros_like(a)
  c=np.zeros_like(a)
  d=np.zeros_like(a)
  b[4,5]=30
  c[4,5]=40
  d[4,5]=60
  activity=probe.temporal_activity([a,b,c,d])
  self.assertGreater(activity[4,5],0)
  self.assertEqual(activity[1,1],0)
 def test_invalid_region(self):
  with self.assertRaises(ValueError):
   probe.rank_regions(np.ones((10,10)),region_xy=(11,8))
 def test_integral_sum(self):
  a=np.arange(25,dtype=np.float32).reshape(5,5)
  self.assertEqual(probe.rect_sum(probe.summed_area(a),1,1,2,2),int(a[1:3,1:3].sum()))

if __name__=="__main__":
 unittest.main()
