"""Independent synthetic CCOR continuity and pixel-evidence regression tests."""
import datetime as dt
import pathlib
import sys
import unittest
import numpy as np
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"tools"))
from ccor_review_evidence import frame_continuity,pixel_at,pixel_evidence,assess

base=dt.datetime(2026,10,25,0,0,tzinfo=dt.timezone.utc)
def stamp(index):
    return (base+dt.timedelta(minutes=15*index)).isoformat().replace("+00:00","Z")
def trail(indices,x=40,y=45):
    return {"first":stamp(min(indices)),"last":stamp(max(indices)),
            "points":[{"time":stamp(i),"x":x,"y":y} for i in indices]}

class EvidenceTests(unittest.TestCase):
    def test_sparse_track_with_three_hour_gap_fails(self):
        t=trail([0,1,2,3,15])
        result=frame_continuity(t,[stamp(i) for i in range(16)])
        self.assertEqual(result["matched"],5)
        self.assertEqual(result["available"],16)
        self.assertEqual(result["longest_missing_run"],11)
        self.assertEqual(result["status"],"FAIL")
    def test_dense_track_can_tolerate_one_dropout(self):
        t=trail([0,1,2,4,5,6,7])
        r=frame_continuity(t,[stamp(i) for i in range(8)])
        self.assertEqual(r["status"],"PASS")
        self.assertEqual(r["longest_missing_run"],1)
    def test_weak_or_extended_pixels_fail(self):
        baseline=np.full((96,96),30,dtype=np.float32)
        empty=baseline.copy()
        self.assertFalse(pixel_at(empty,baseline,40,45)["pass"])
        band=baseline.copy()
        band[40:51,37:44]=90
        self.assertFalse(pixel_at(band,baseline,40,45)["pass"])
    def test_compact_point_source_passes(self):
        baseline=np.full((96,96),30,dtype=np.float32)
        img=baseline.copy()
        img[45,40]=80
        img[45,41]=53
        self.assertTrue(pixel_at(img,baseline,40,45)["pass"])
    def test_full_assessment_requires_both(self):
        baseimg=np.full((96,96),30,dtype=np.float32)
        point=baseimg.copy()
        point[45,40]=80
        t=trail(list(range(8)))
        allframes={stamp(i):point for i in range(8)}
        self.assertEqual(assess(t,[stamp(i) for i in range(8)],allframes,baseimg)["status"],"PASS")
        blank=dict(allframes)
        for i in range(6): blank[stamp(i)]=baseimg
        self.assertEqual(assess(t,[stamp(i) for i in range(8)],blank,baseimg)["status"],"FAIL")
    def test_missing_cache_unassessable(self):
        t=trail([0,1,2,3])
        self.assertEqual(pixel_evidence(t,{},np.zeros((96,96)))["status"],"UNASSESSABLE")

if __name__=="__main__":
    unittest.main()
