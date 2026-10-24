"""Time shuffle CCOR null controls must not confuse reordered frames with truth."""
import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import ccor_temporal_null_controls as control

class TemporalNullTests(unittest.TestCase):
 def test_permutation_not_identity_or_reversed(self):
  rng=np.random.default_rng(5)
  result=control.shuffle_indices(13,rng)
  self.assertEqual(sorted(result),list(range(13)))
  self.assertNotEqual(result,list(range(13)))
  self.assertNotEqual(result,list(reversed(range(13))))
 def test_low_frame_count_rejected(self):
  with self.assertRaises(ValueError):
   control.shuffle_indices(5,np.random.default_rng(3))
 def test_evidence_fields(self):
  self.assertEqual(control.evidence_tuple(None),(0,0.0,float("-inf")))
  row={"frames":7,"first":"2026-10-22T00:00:00Z",
       "last":"2026-10-22T01:30:00Z","rms_px":0.8}
  self.assertEqual(control.evidence_tuple(row),(7,1.5,-0.8))
if __name__=="__main__":
 unittest.main()
