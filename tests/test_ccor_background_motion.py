"""Synthetic checks for conservative spatially distributed CCOR star drift."""
import pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"tools"))
from ccor_background_motion import estimate_common_motion, annotate_background_motion

def track(n,x,y,vx=4.0,vy=-0.4,camera="ccor1"):
    return dict(id="T"+str(n),camera=camera,x_first=x,y_first=y,
        vx_px_h=vx,vy_px_h=vy,frames=8,rms_px=0.5,
        first="2026-10-24T07:00:00Z",last="2026-10-24T10:00:00Z",
        review_motion_evidence={"status":"MEASURABLE_MOTION"},
        motion_consistency={"status":"PROVISIONALLY_CONSISTENT"})

class BackgroundMotionTests(unittest.TestCase):
    def setUp(self):
        self.stars=[track(i,x,y) for i,(x,y) in enumerate(
            [(80,80),(180,92),(300,110),(420,130),(100,280),(300,380)])]
    def test_spatially_distributed_common_motion(self):
        common=estimate_common_motion(self.stars,"ccor1")
        self.assertIsNotNone(common)
        self.assertEqual(common["vx_px_h"],4.0)
        self.assertGreaterEqual(common["independent_regions"],5)
        objects=annotate_background_motion(self.stars,"ccor1",common)
        self.assertTrue(all(x["background_motion"]["status"]=="LIKELY_BACKGROUND_STAR_MOTION" for x in objects))
    def test_independent_comet_must_survive(self):
        comet=track(99,270,260,12,5)
        rows=self.stars+[comet]
        common=estimate_common_motion(rows,"ccor1")
        self.assertIsNotNone(common)
        got=annotate_background_motion(rows,"ccor1",common)
        self.assertEqual(got[-1]["background_motion"]["status"],"DIFFERENT_FROM_COMMON_MOTION")
    def test_clustered_duplicate_artifacts_not_enough(self):
        similar=[track(i,95+(i%3)*4,96+(i//3)*5) for i in range(25)]
        self.assertIsNone(estimate_common_motion(similar,"ccor1"))
    def test_too_few_background_sources_not_enough(self):
        self.assertIsNone(estimate_common_motion(self.stars[:4],"ccor1"))
        got=annotate_background_motion(self.stars[:4],"ccor1",None)
        self.assertEqual(got[0]["background_motion"]["status"],"BACKGROUND_UNDETERMINED")
    def test_cross_camera_never_combined(self):
        mixed=self.stars[:3]+[track(i+20,x,y,camera="ccor2") for i,(x,y) in enumerate(
            [(80,80),(180,92),(300,110),(420,130),(100,280),(300,380)])]
        self.assertIsNone(estimate_common_motion(mixed,"ccor1"))
        self.assertIsNotNone(estimate_common_motion(mixed,"ccor2"))
    def test_moving_image_stripes_in_one_spatial_zone_not_star_field(self):
        same=[track(i,210+i%4*2,160+i//4*3) for i in range(32)]
        self.assertIsNone(estimate_common_motion(same,"ccor1"))

if __name__=="__main__":
    unittest.main()
