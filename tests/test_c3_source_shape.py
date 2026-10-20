"""Synthetic compact comet versus elongated streak-edge controls."""
import sys,unittest
from pathlib import Path
try:
 import numpy as np
except ImportError:
 raise unittest.SkipTest("numpy is installed in the dedicated shadow CI")
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from audit_c3_source_shape import source_shape,shape_summary,decode_gif_as_512

class SourceShapeTests(unittest.TestCase):
 def test_bright_compact_source(self):
  y,x=np.mgrid[:512,:512]
  a=np.full((512,512),80,dtype=float)
  a+=75*np.exp(-((x-190)**2+(y-350)**2)/(2*1.2**2))
  row=source_shape(a.astype(np.uint8),190,350)
  self.assertGreater(row["snr"],3)
  self.assertTrue(row["compact_hit"])
 def test_elongated_edge_not_compact(self):
  y,x=np.mgrid[:512,:512]
  a=np.full((512,512),80,dtype=float)
  a+=75*np.exp(-((x-95)**2)/(2*1.3**2))*np.exp(-((y-360)**2)/(2*8**2))
  row=source_shape(a.astype(np.uint8),95,360)
  self.assertGreater(row["snr"],3)
  self.assertGreater(row["elongation"],4)
  self.assertFalse(row["compact_hit"])
 def test_missing_and_outer_path(self):
  a=np.full((512,512),80,dtype=np.uint8)
  self.assertIsNone(source_shape(a,3,400))
  self.assertEqual(shape_summary([None])["samples"],0)
 def test_no_signal(self):
  a=np.full((512,512),80,dtype=np.uint8)
  row=source_shape(a,200,350)
  self.assertFalse(row["compact_hit"])
if __name__=="__main__":unittest.main()
