"""Synthetic moving-dark-trough and real-moving-source controls."""
import sys,unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from audit_c3_moving_streak import shadow_center_in_frame, streak_drift_measurements, following_evidence

class MovingShadowTests(unittest.TestCase):
    def test_drifting_streak_trough_measured(self):
        frames=[]
        for i in range(6):
            a=np.full((512,512),120,dtype=np.uint8)
            a[320:384,75+i:86+i]=35
            a[345,175+i*3]=245
            frames.append(a)
        measurements=[shadow_center_in_frame(a,85,320,384) for a in frames]
        self.assertTrue(all(x["measured"] for x in measurements))
        positions=[x["x"] for x in measurements]
        self.assertGreaterEqual(max(positions)-min(positions),3)
    def test_genuine_comet_away_from_streak_not_following(self):
        from datetime import datetime,timezone,timedelta
        t0=datetime(2026,10,20,tzinfo=timezone.utc)
        rows=[{"time":t0+timedelta(hours=i),"shadow":{"measured":True,"x":80+i}}
              for i in range(6)]
        track={"first":{"time":t0,"x":172,"y":340},
               "last":{"time":t0+timedelta(hours=5),"x":192,"y":340}}
        region={"y_start":320,"y_end":384}
        evidence=following_evidence(track,rows,region)
        self.assertTrue(evidence["evaluable"])
        self.assertFalse(evidence["co_moving_near_shadow"])
    def test_shadow_trajectory_flagged_but_not_removed(self):
        from datetime import datetime,timezone,timedelta
        t0=datetime(2026,10,20,tzinfo=timezone.utc)
        rows=[{"time":t0+timedelta(hours=i),"shadow":{"measured":True,"x":80+i}}
              for i in range(6)]
        track={"first":{"time":t0,"x":86,"y":340},
               "last":{"time":t0+timedelta(hours=5),"x":91,"y":340}}
        evidence=following_evidence(track,rows,{"y_start":320,"y_end":384})
        self.assertTrue(evidence["evaluable"])
        self.assertTrue(evidence["co_moving_near_shadow"])
if __name__=="__main__":unittest.main()
