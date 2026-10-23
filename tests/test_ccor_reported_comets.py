"""CCOR reported-candidate replay never equates uncalibrated coordinates."""
import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import benchmark_ccor_reported_comets as benchmark

class CCORReplayTests(unittest.TestCase):
    def test_midnight_rollover(self):
        case=benchmark.CASES["ccor2-20261022-wage"]
        times=[benchmark.timestamp(t) for t,_,_ in case["points"]]
        self.assertEqual(times[0].date(),dt.date(2026,10,22))
        self.assertEqual(times[-1].date(),dt.date(2026,10,23))
        self.assertEqual([int((b-a).total_seconds()/60) for a,b in zip(times,times[1:])],[45,30,15])
    def test_calibration_is_required(self):
        case=benchmark.CASES["ccor1-20261022-ruan"]
        with self.assertRaises(ValueError):
            benchmark.projected_native_point(1530,1208,case["native_shape"],[512,512],[1204,1205],{})
    def test_cross_camera_not_scored(self):
        case=benchmark.CASES["ccor1-20261022-ruan"]
        fake=[{"camera":"ccor2","id":"x"}]
        cal={"left":0,"top":0,"width":1204,"height":1205,"origin":"upper_left"}
        self.assertEqual(benchmark.compare_with_calibration(case,fake,cal),[])
    def test_frame_window(self):
        start,end=benchmark.frame_window(benchmark.CASES["ccor2-20261022-wage"])
        self.assertLess(start,benchmark.timestamp("2026-10-22T23:45:00Z"))
        self.assertGreater(end,benchmark.timestamp("2026-10-23T01:15:00Z"))
if __name__=="__main__":
    unittest.main()
