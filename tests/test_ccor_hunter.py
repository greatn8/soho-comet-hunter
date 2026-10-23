"""Offline regression tests for the isolated CCOR review hunter."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import ccor_hunter as ch

class CCORTests(unittest.TestCase):
    def test_index_discovery_and_camera_isolation(self):
        now=dt.datetime(2026,10,23,12,tzinfo=dt.timezone.utc)
        data=b'<a href="20261023_1100_ccor1_1024by960.jpg">one</a> <a href="20261023_1100_ccor2_1024by960.jpg">two</a>'
        found,errors=ch.discover("ccor1",now,fetch=lambda url:data)
        self.assertEqual([x["name"] for x in found],["20261023_1100_ccor1_1024by960.jpg"])
        self.assertFalse(errors)
    def test_no_cross_camera_coordinate_matching(self):
        a={"camera":"ccor1","first":"2026-10-23T10:00:00Z","x_first":140,"y_first":120,"vx_px_h":1,"vy_px_h":1}
        b={**a,"camera":"ccor2"}
        self.assertFalse(ch.match_track(a,b,None,None))
        self.assertTrue(ch.match_track(a,a,None,None))
    def test_distinct_id_integration(self):
        now=dt.datetime(2026,10,23,12,tzinfo=dt.timezone.utc)
        a={"id":"CCOR1-123","camera":"ccor1","first":"2026-10-23T10:00:00Z","last":"2026-10-23T11:00:00Z","frames":5,"x_first":140,"y_first":120,"vx_px_h":1,"vy_px_h":1}
        b={**a,"id":"CCOR1-456","frames":7}
        found=ch.integrate_events([a],[b],"ccor1",now)
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]["id"],"CCOR1-123")
        self.assertEqual(found[0]["frames"],7)
    def test_duplicate_prior_ids_are_consolidated_without_losing_best_fit(self):
        now=dt.datetime(2026,10,23,12,tzinfo=dt.timezone.utc)
        base={"id":"CCOR1-same","camera":"ccor1","first":"2026-10-23T10:00:00Z",
              "last":"2026-10-23T11:00:00Z","frames":4,"rms_px":1.2,
              "x_first":140,"y_first":120,"vx_px_h":1,"vy_px_h":1}
        better={**base,"frames":8,"rms_px":0.3}
        got=ch.integrate_events([base,better],[],"ccor1",now)
        self.assertEqual(len(got),1)
        self.assertEqual(got[0]["frames"],8)

    def test_review_shortlist_keeps_spatially_distinct_track(self):
        common={"camera":"ccor1","first":"2026-10-23T10:00:00Z",
                "last":"2026-10-23T11:00:00Z","frames":5,"rms_px":0.8,
                "processing_shape":[512,512]}
        crowded=[{**common,"id":f"crowded-{i}","x_first":150,"y_first":420}
                 for i in range(10)]
        distinct={**common,"id":"distinct","x_first":430,"y_first":120}
        picked=ch.review_shortlist(crowded+[distinct],limit_per_camera=5)
        self.assertEqual(len(picked),5)
        self.assertIn("distinct",picked)
        self.assertEqual(len(set(picked)),5)

    def test_offline_writes_no_alerts(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload=ch.run_once(Path(tmp),now=dt.datetime(2026,10,23,12,tzinfo=dt.timezone.utc),offline=True)
            self.assertEqual(payload["mode"],"EXPERIMENTAL_REVIEW_ONLY")
            self.assertFalse(payload["automatic_email_alerts"])
            self.assertTrue((Path(tmp)/"results/ccor/ccor.json").exists())
            self.assertFalse((Path(tmp)/"results/realtime").exists())

if __name__=="__main__":
    unittest.main()
