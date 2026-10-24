"""Synthetic motion controls: preserve smooth motion and flag jumps."""
import datetime as dt
import pathlib
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from ccor_motion_consistency import motion_consistency

def track(xys, minutes=None):
    base=dt.datetime(2026,10,24,7,tzinfo=dt.timezone.utc)
    minutes=minutes or [15*i for i in range(len(xys))]
    return {"camera":"ccor2","points":[{"time":(base+dt.timedelta(minutes=m)).isoformat().replace("+00:00","Z"),"x":x,"y":y} for (x,y),m in zip(xys,minutes)]}

class MotionConsistencyTests(unittest.TestCase):
    def test_straight_path_survives(self):
        a=track([(100+3*i,200+2*i) for i in range(8)])
        self.assertEqual(motion_consistency(a)["status"],"PROVISIONALLY_CONSISTENT")
    def test_gentle_curvature_survives(self):
        a=track([(100+i*3,200+i*i*.08) for i in range(8)])
        self.assertEqual(motion_consistency(a)["status"],"PROVISIONALLY_CONSISTENT")
    def test_pixel_rounding_survives(self):
        a=track([(100,100),(101,101),(101,101),(102,102),(103,103),(104,104)])
        self.assertEqual(motion_consistency(a)["status"],"PROVISIONALLY_CONSISTENT")
    def test_jump_flagged(self):
        a=track([(100,100),(103,101),(106,102),(109,103),(128,114),(115,105),(118,106)])
        self.assertEqual(motion_consistency(a)["status"],"IRREGULAR_MOTION")
    def test_reversal_flagged(self):
        a=track([(100,100),(104,100),(108,100),(104,100),(100,100)])
        self.assertEqual(motion_consistency(a)["status"],"IRREGULAR_MOTION")
    def test_original_example_flagged(self):
        a=track([(246,173),(245,171),(242,170),(238,163),(238,155),(234,153),(234,149),(233,148)],
                [0,15,30,90,135,150,180,195])
        self.assertEqual(motion_consistency(a)["status"],"IRREGULAR_MOTION")
    def test_invalid_times_unassessable(self):
        a=track([(100,100)]*4,[0,15,15,45])
        self.assertEqual(motion_consistency(a)["status"],"UNASSESSABLE")
if __name__=="__main__":
    unittest.main()
