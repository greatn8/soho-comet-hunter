"""Synthetic positive/negative temporal contrast and safety checks."""
import sys,unittest
from pathlib import Path
try:
 import numpy as np
except ImportError:
 raise unittest.SkipTest("numpy required by dedicated temporal diagnostics CI")
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from audit_official_c3_temporal_512 import transformed,temporal_template,inspect_frame,stats
class TemporalSourceTests(unittest.TestCase):
 def test_moving_bright_source_recovers_from_stationary_background(self):
  frames=[]
  for i in range(9):
   a=np.full((512,512),110,dtype=np.uint8)
   a[350,140+10*i]=160
   frames.append(a)
  median=temporal_template(frames)
  row=inspect_frame(frames[4],median,(180,350),18,"temporal_bright")
  self.assertTrue(row["raw_hit"])
 def test_static_bright_source_vanishes_in_temporal_difference(self):
  frames=[]
  for i in range(9):
   a=np.full((512,512),100,dtype=np.uint8)
   a[350,175]=250
   frames.append(a)
  median=temporal_template(frames)
  self.assertTrue(inspect_frame(frames[0],median,(175,350),18,"raw_bright")["raw_hit"])
  self.assertFalse(inspect_frame(frames[0],median,(175,350),18,"temporal_bright")["raw_hit"])
 def test_negative_residuals_are_not_bright_residuals(self):
  a=np.full((512,512),100,dtype=np.uint8)
  median=np.full((512,512),100,dtype=np.float32)
  a[360,130]=30
  self.assertFalse(inspect_frame(a,median,(130,360),18,"temporal_bright")["raw_hit"])
  self.assertTrue(inspect_frame(a,median,(130,360),18,"temporal_dark")["raw_hit"])
 def test_counts_and_control_hits_are_defined(self):
  row={"in_field":True,"raw_hit":True,"spatial_selected_hit":False,
   "control_hits":[False,False,True,False],"raw_peak_count":2,
   "selected_peak_count":1,"lower_left_peak_fraction":0.5}
  result=stats([row])
  self.assertEqual(result["truth_in_cuda_field"],1)
  self.assertEqual(result["control_hit_pct"],25)
if __name__=="__main__":unittest.main()
