"""Opt-in global CCOR ranking must leave the legacy detector unchanged."""
import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import ccor_hunter as hunter
import ccor_blind_uncapped as offline

class CCORGlobalRankTests(unittest.TestCase):
 def test_synthetic_original_and_complete_agree(self):
  rng=np.random.default_rng(72)
  frames=[rng.normal(40,2,(128,128)).astype("float32") for _ in range(8)]
  times=[dt.datetime(2026,10,22,23,0,tzinfo=dt.timezone.utc)+dt.timedelta(minutes=15*i) for i in range(8)]
  size=[(128,128)]*8
  for i,arr in enumerate(frames):
   arr[30,40+i]+=120
  original=hunter.track_points(frames,times,size,"ccor2")
  legacy,info=hunter.track_points(frames,times,size,"ccor2",return_diagnostics=True)
  self.assertEqual(original,legacy)
  self.assertFalse(info["complete_search"])
  global_tracks,stats=hunter.track_points(frames,times,size,"ccor2",complete_search=True,return_diagnostics=True)
  all_tracks,offline_stats=offline.scan_without_early_return(frames,times,size,"ccor2")
  self.assertTrue(stats["scan_completed"])
  # Complete search now checks pixel and continuity evidence before top 40.
  # Strip per-track diagnostics when comparing the underlying hypotheses.
  self.assertEqual(
      [{k:v for k,v in row.items() if not k.startswith("precap_")} for row in global_tracks],
      sorted(all_tracks,key=hunter.track_quality,reverse=True)[:40])
  self.assertEqual(stats["distinct_hypotheses"],len(all_tracks))
 def test_legacy_first40_preserved_and_complete_can_pick_later(self):
  rng=np.random.default_rng(22)
  frames=[rng.normal(10,1,(128,128)).astype("float32") for _ in range(8)]
  times=[dt.datetime(2026,10,22,23,0,tzinfo=dt.timezone.utc)+dt.timedelta(minutes=15*i) for i in range(8)]
  size=[(128,128)]*8
  original=hunter.track_points(frames,times,size,"ccor1")
  complete,meta=hunter.track_points(frames,times,size,"ccor1",complete_search=True,return_diagnostics=True)
  self.assertTrue(meta["scan_completed"])
  self.assertEqual(len(complete),0)
  self.assertEqual(original,[])
 def test_short_input_reports_complete(self):
  tracks,info=hunter.track_points([],[],[],"ccor1",complete_search=True,return_diagnostics=True)
  self.assertEqual(tracks,[])
  self.assertTrue(info["scan_completed"])

if __name__=="__main__":
 unittest.main()
