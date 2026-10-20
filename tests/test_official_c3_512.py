"""Unit checks for resolution-correct independently labelled comet replay."""
import sys,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from benchmark_official_c3_512 import truth_at_512,evaluate,FLAGS

class KnownComet512Tests(unittest.TestCase):
 def test_truth_coordinates_halved_not_stretched(self):
  at=datetime(2008,1,1,tzinfo=timezone.utc)
  inp=[("f.gif",{"time":at,"x":510.0,"y":330.0})]
  result=truth_at_512(inp)
  self.assertEqual(result[0]["x"],255)
  self.assertEqual(result[0]["y"],165)
 def test_true_track_recovered_at_512_and_top100(self):
  t=datetime(2008,1,1,tzinfo=timezone.utc)
  truth=[{"time":t+timedelta(minutes=12*i),"x":140+i*2,"y":200+i}
         for i in range(9)]
  c={"id":"C1","priority":"HIGH","frames":9,
     "first":{"time":truth[0]["time"],"x":140,"y":200},
     "last":{"time":truth[-1]["time"],"x":156,"y":208}}
  out=evaluate([c],truth,500)
  self.assertTrue(out["known_comet_recovered_in_full_pool"])
  self.assertTrue(out["known_comet_recovered_in_top_100"])
  self.assertEqual(out["first_matched_track_rank"],1)
 def test_wrong_position_not_recovered(self):
  t=datetime(2008,1,1,tzinfo=timezone.utc)
  truth=[{"time":t+timedelta(minutes=12*i),"x":140+i*2,"y":200+i}
         for i in range(9)]
  c={"id":"C1","priority":"HIGH","frames":9,
     "first":{"time":truth[0]["time"],"x":190,"y":200},
     "last":{"time":truth[-1]["time"],"x":206,"y":208}}
  self.assertFalse(evaluate([c],truth,500)["known_comet_recovered_in_full_pool"])
 def test_exact_production_enhanced_filters_included(self):
  self.assertEqual(FLAGS,("--within-window-stationary-filter",
                          "--spatial-balanced-peaks",
                          "--trajectory-integrity-filter",
                          "--trajectory-null-control"))
if __name__=="__main__":unittest.main()
