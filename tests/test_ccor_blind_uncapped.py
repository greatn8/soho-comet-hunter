"""Parity tests for exhaustive seed scan vs original capped production scan."""
import datetime as dt,sys,unittest
from pathlib import Path
import numpy as np
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import ccor_blind_uncapped as uncapped
import ccor_hunter as hunter

class Tests(unittest.TestCase):
 def test_same_first40_as_production(self):
  rng=np.random.default_rng(73)
  times=[dt.datetime(2026,10,22,23,0,tzinfo=dt.timezone.utc)+
         dt.timedelta(minutes=15*i) for i in range(8)]
  frames=[rng.normal(20,2,(96,96)).astype("float32") for _ in times]
  for i,arr in enumerate(frames):
   x=20+i
   arr[25,x]+=80
  sizes=[(96,96)]*8
  original=hunter.track_points(frames,times,sizes,"ccor2")
  expanded,stats=uncapped.scan_without_early_return(
      frames,times,sizes,"ccor2",max_seed_pairs=150000)
  self.assertTrue(stats["search_complete"])
  self.assertEqual(expanded[:min(40,len(expanded))],original)
 def test_budget_is_not_called_complete(self):
  rng=np.random.default_rng(22)
  frames=[rng.normal(20,15,(150,150)).astype("float32") for _ in range(8)]
  times=[dt.datetime(2026,10,22,23,0,tzinfo=dt.timezone.utc)+
         dt.timedelta(minutes=15*i) for i in range(8)]
  with patch.object(uncapped.h,"peaks",return_value=[(20.,20.,15.),(40.,40.,15.)]):
   _,s=uncapped.scan_without_early_return(
       frames,times,[(150,150)]*8,"ccor2",max_seed_pairs=1)
  self.assertFalse(s["search_complete"])
  self.assertTrue(s["budget_exceeded"])
if __name__=="__main__":unittest.main()
