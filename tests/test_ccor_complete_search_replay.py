"""Offline CCOR A/B summary and data-integrity tests."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import ccor_complete_search_replay as replay

class OfflineABTests(unittest.TestCase):
 def test_new_hypotheses_not_comet_claim(self):
  legacy=[{"id":"a"}]
  complete=[{"id":"b"}]
  meta={"scan_completed":True,"distinct_hypotheses":2}
  with self.assertRaises(KeyError):
   replay.summarize(legacy,complete,meta,meta)
 def test_empty_scan_is_complete(self):
  meta={"scan_completed":True,"distinct_hypotheses":0}
  out=replay.summarize([],[],meta,meta)
  self.assertEqual(out["status"],"COMPLETE_AB_COMPARISON")
  self.assertEqual(out["new_top40_count"],0)
  self.assertIsNone(out["reported_comet_recovered"])
 def test_incomplete_scan_not_accepted(self):
  meta={"scan_completed":False,"distinct_hypotheses":5000,
        "stop_reason":"COMPLETE_SEARCH_BUDGET_EXCEEDED"}
  out=replay.summarize([],[],meta,meta)
  self.assertEqual(out["status"],"INCOMPLETE_SEARCH_BUDGET")

if __name__=="__main__":
 unittest.main()
