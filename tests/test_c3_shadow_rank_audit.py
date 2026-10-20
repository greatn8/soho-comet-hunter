"""Small synthetic controls for offline streak-associated reranking."""
import sys, unittest
from pathlib import Path
try:
    import numpy as np
except ImportError:
    raise unittest.SkipTest("numpy only installed in dedicated shadow diagnostic CI")
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from audit_c3_shadow_ranking import track_artifact_evidence, ranking_order, audit

class ShadowRankTests(unittest.TestCase):
    def track(self,x,y,score,dx=0,dy=12):
        return {"first":{"x":x,"y":y},"last":{"x":x+dx,"y":y+dy},
                "score":score,"frames":8,"speed":2,"vx":0,"vy":2,
                "rms":0.3,"cv":0.1,"sunward":3}

    def test_persistent_shadow_flag_requires_excess_against_controls(self):
        feature=np.zeros((512,512),dtype=bool)
        feature[300:420,95:105]=True
        track=self.track(100,340,600)
        row=track_artifact_evidence(track,feature)
        self.assertTrue(row["evaluable"])
        self.assertTrue(row["associated_with_persistent_shadow"])
        self.assertGreater(row["excess_fraction"],0.5)

    def test_uniform_feature_not_specific(self):
        feature=np.zeros((512,512),dtype=bool)
        feature[300:420,30:245]=True
        row=track_artifact_evidence(self.track(100,340,600),feature)
        self.assertTrue(row["evaluable"])
        self.assertFalse(row["associated_with_persistent_shadow"])

    def test_no_mask_for_moving_object_away_from_streak(self):
        feature=np.zeros((512,512),dtype=bool)
        feature[300:420,95:105]=True
        row=track_artifact_evidence(self.track(180,340,500),feature)
        self.assertTrue(row["evaluable"])
        self.assertFalse(row["associated_with_persistent_shadow"])

    def test_rank_only_no_candidate_deletion(self):
        mask=np.zeros((512,512),dtype=bool)
        mask[300:420,95:105]=True
        tracks=[self.track(100,340,600),self.track(180,340,500)]
        entries=[]
        from realtime_hunter import candidate_quadrant
        for rank,t in enumerate(tracks,1):
            entries.append({"track":t,"native_rank":rank,
                "quadrant":candidate_quadrant(t),
                "shadow":track_artifact_evidence(t,mask),
                "comet_like":True})
        original=ranking_order(entries,"native")
        after=ranking_order(entries,"streak_last")
        self.assertEqual(len(after),len(original))
        self.assertEqual(after[0]["native_rank"],2)
        self.assertEqual({id(i) for i in after},{id(i) for i in original})

if __name__=="__main__":unittest.main()
