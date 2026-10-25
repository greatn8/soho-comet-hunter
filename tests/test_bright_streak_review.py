"""Synthetic saturated planet streak control tests for C3 reviews."""
import datetime as dt
import pathlib
import sys
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"tools"))
from bright_streak_review import near_horizontal_streak,bright_streak_review_evidence

class BrightStreakTests(unittest.TestCase):
    def test_bright_line_near_track(self):
        image=bytearray(512*512)
        for x in range(25,480): image[300*512+x]=255
        self.assertIsNotNone(near_horizontal_streak(image,512,260,289))
        self.assertIsNone(near_horizontal_streak(image,512,260,260))
        self.assertIsNone(near_horizontal_streak(image,512,10,300))
    def test_isolated_star_does_not_trigger(self):
        image=bytearray(512*512)
        image[300*512+260]=255
        self.assertIsNone(near_horizontal_streak(image,512,260,300))
    def test_scaled_1024_streak(self):
        image=bytearray(1024*1024)
        image[600*1024+100:600*1024+970]=bytes([255])*870
        self.assertIsNotNone(near_horizontal_streak(image,1024,520,585))
    def test_only_persistent_proximity_downgrades(self):
        t0=dt.datetime(2026,10,25,tzinfo=dt.timezone.utc)
        times=[t0+dt.timedelta(minutes=15*i) for i in range(5)]
        paths=[pathlib.Path(t.strftime("%Y%m%d_%H%M")+"_c3_512.jpg") for t in times]
        strong=bytearray(512*512)
        strong[300*512+35:300*512+485]=bytes([255])*450
        blank=bytes(512*512)
        c={"first":{"time":times[0],"x":260,"y":289},"last":{"time":times[-1]},
           "vx":0.0,"vy":0.0}
        by_name={p.name:strong for p in paths}
        get=lambda path,cache:by_name[path.name]
        result=bright_streak_review_evidence(c,paths,{},get,512)
        self.assertEqual(result["status"],"LIKELY_STREAK_ARTIFACT")
        self.assertEqual(result["frames_near_streak"],5)
        for p in paths[1:]:by_name[p.name]=blank
        result=bright_streak_review_evidence(c,paths,{},get,512)
        self.assertEqual(result["status"],"CLEAR")
    def test_unavailable_is_not_automatically_rejected(self):
        self.assertEqual(bright_streak_review_evidence({},[],{},lambda a,b:None,512)["status"],"UNAVAILABLE")

if __name__=="__main__":unittest.main()
