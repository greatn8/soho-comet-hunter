"""Star-field consensus checks. No network or GPU required."""
import datetime as dt
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from star_field import annotate_star_field, star_field_evidence


def track(i,x,y,vx=3.0,vy=-1.0,size=1024,minutes=180):
    t=dt.datetime(2026,10,21,12,tzinfo=dt.timezone.utc)
    return {"cid":f"C{i}","resolution":size,"source_window_hours":12,
            "first":{"x":x*size/512,"y":y*size/512,"time":t},
            "last":{"x":(x+vx*minutes/60)*size/512,
                    "y":(y+vy*minutes/60)*size/512,
                    "time":t+dt.timedelta(minutes=minutes)},
            "vx":vx*size/512,"vy":vy*size/512,"trajectory_warning":"",
            "quality_issues":[],"pixel_verification":{"status":"STRONG",
            "hits":14,"samples":14,"hit_fraction":1.0}}


class StarFieldTests(unittest.TestCase):
    def test_broad_starfield_consensus(self):
        refs=[track(i,x,y) for i,(x,y) in enumerate(
            [(45,50),(130,55),(225,45),(315,60),(85,220),(290,245),(400,315)],1)]
        comet=track(99,200,390,-2,-3)
        annotate_star_field(refs+[comet])
        self.assertEqual(refs[0]["star_field"]["status"],"STAR_LIKE")
        self.assertNotEqual(comet["star_field"]["status"],"STAR_LIKE")

    def test_two_resolutions_give_same_decision(self):
        pts=[(50,50),(150,75),(255,70),(350,95),(80,220),(250,260),(400,350)]
        for size in (512,1024):
            refs=[track(i,x,y,size=size) for i,(x,y) in enumerate(pts,1)]
            self.assertEqual(star_field_evidence(refs[0],refs)["status"],"STAR_LIKE")

    def test_duplicate_hypotheses_cannot_create_starfield(self):
        original=track(1,200,200)
        refs=[original]+[track(i,201+i*.1,201) for i in range(2,20)]
        self.assertNotEqual(star_field_evidence(original,refs)["status"],"STAR_LIKE")

    def test_nonoverlapping_and_weak_tracks_do_not_vote(self):
        t=track(1,25,25)
        refs=[t]+[track(i,30+i*70,250) for i in range(2,9)]
        for other in refs[1:]:
            other["pixel_verification"]["status"]="EMPTY"
        self.assertNotEqual(star_field_evidence(t,refs)["status"],"STAR_LIKE")

    def test_no_fixed_direction_or_velocity_is_assumed(self):
        refs=[track(i,x,y,vx=-4,vy=2) for i,(x,y) in enumerate(
            [(45,50),(130,55),(225,45),(315,60),(85,220),(290,245)],1)]
        self.assertEqual(star_field_evidence(refs[0],refs)["status"],"STAR_LIKE")


if __name__=="__main__":
    unittest.main()
