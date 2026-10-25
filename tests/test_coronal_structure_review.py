"""Regression tests for coronal streamer triage and comet protection."""
import datetime as dt
import pathlib
import sys
import unittest
import numpy as np
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"tools"))
from coronal_structure_review import radial_structure_sample,coronal_structure_evidence

class CoronalStructureTests(unittest.TestCase):
    def setUp(self):
        self.resolution=512
        yy,xx=np.ogrid[:512,:512]
        self.broad=np.full((512,512),6,dtype=np.uint8)
        # Extended constant radial ray aimed away from the Sun at (256,256).
        self.broad[(abs(yy-256)<=5)&(xx>=320)&(xx<470)]=180
        self.compact=np.full((512,512),6,dtype=np.uint8)
        self.compact[255:258,389:392]=230
    def test_broad_ray_detected_without_brightness_cutoff(self):
        self.assertTrue(radial_structure_sample(self.broad.tobytes(),512,390,256)["broad"])
        self.assertFalse(radial_structure_sample(self.compact.tobytes(),512,390,256)["broad"])
    def test_bright_compact_comet_retained(self):
        r=radial_structure_sample(self.compact.tobytes(),512,390,256)
        self.assertFalse(r["broad"])
    def test_persistent_structure_with_failed_motion_downgraded(self):
        start=dt.datetime(2026,10,25,tzinfo=dt.timezone.utc)
        ts=[start+dt.timedelta(minutes=15*i) for i in range(5)]
        paths=[pathlib.Path(t.strftime("%Y%m%d_%H%M")+"_c3_512.jpg") for t in ts]
        c={"first":{"time":ts[0],"x":390,"y":256},"last":{"time":ts[-1]},
           "vx":0,"vy":0,"pixel_verification":{"temporal_motion":{
             "status":"MEASURED","tested":6,"supported":0}}}
        frame={str(p):self.broad.tobytes() for p in paths}
        decoder=lambda p,cache:frame[str(p)]
        r=coronal_structure_evidence(c,paths,{},decoder,512)
        self.assertEqual(r["status"],"LIKELY_CORONAL_STRUCTURE")
        c["pixel_verification"]["temporal_motion"]["supported"]=2
        self.assertEqual(coronal_structure_evidence(c,paths,{},decoder,512)["status"],"BROAD_BUT_MOVING")
        c["pixel_verification"]["temporal_motion"]["status"]="UNAVAILABLE"
        self.assertEqual(coronal_structure_evidence(c,paths,{},decoder,512)["status"],"MOTION_UNASSESSABLE")
    def test_short_crossing_kept(self):
        start=dt.datetime(2026,10,25,tzinfo=dt.timezone.utc)
        ts=[start+dt.timedelta(minutes=15*i) for i in range(5)]
        paths=[pathlib.Path(t.strftime("%Y%m%d_%H%M")+"_c3_512.jpg") for t in ts]
        frames={str(p):self.compact.tobytes() for p in paths}
        frames[str(paths[2])]=self.broad.tobytes()
        c={"first":{"time":ts[0],"x":390,"y":256},"last":{"time":ts[-1]},
           "vx":0,"vy":0,"pixel_verification":{"temporal_motion":{
              "status":"MEASURED","tested":6,"supported":0}}}
        result=coronal_structure_evidence(c,paths,{},lambda p,cache:frames[str(p)],512)
        self.assertEqual(result["status"],"CLEAR")

if __name__=="__main__": unittest.main()
