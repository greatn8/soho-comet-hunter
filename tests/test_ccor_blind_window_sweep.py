"""Multi-window blind cap bypass uses unchanged production CCOR functions."""
import datetime as dt
import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import ccor_blind_window_sweep as s

class TestBlindSweep(unittest.TestCase):
 def test_windows_includes_end(self):
  w=s.windows(15)
  self.assertIn((0,7),w)
  self.assertIn((8,15),w)
  self.assertIn((2,15),w)
  self.assertTrue(all(b-a in s.SIZES for a,b in w))
 def test_dedup_prefers_quality(self):
  common={"camera":"ccor2","first":"2026-10-22T23:00:00Z",
          "last":"2026-10-23T00:00:00Z",
          "x_first":40,"y_first":50,"vx_px_h":2,"vy_px_h":2,"rms_px":1}
  weak={**common,"id":"weak","frames":4}
  strong={**common,"id":"strong","frames":6}
  with patch.object(s.hunter,"match_track",return_value=True):
   result=s.deduplicate([weak,strong])
  self.assertEqual([r["id"] for r in result],["strong"])
if __name__=="__main__":unittest.main()
