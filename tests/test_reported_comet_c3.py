"""Independent reported 2024 SOHO C3 comet reference matching tests."""
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from benchmark_reported_comet_c3 import (
    RAW_POINTS, compare_track, rank_against_report, truth_positions,
)

class ReportedCometTests(unittest.TestCase):
    def test_five_original_report_positions_and_origin_alternatives(self):
        self.assertEqual(len(RAW_POINTS),5)
        a=truth_positions("upper_left")
        b=truth_positions("lower_left")
        self.assertEqual([x for _,x,_ in a],[670,669,668,667,666])
        self.assertEqual([y for _,_,y in a],[910,908,907,906,904])
        self.assertEqual([y for _,_,y in b],[114,116,117,118,120])
        self.assertTrue(all(a[i][0]<a[i+1][0] for i in range(4)))

    def test_track_approximating_report_is_a_provisional_match(self):
        truth=truth_positions("upper_left")
        track={
            "id":"C01","frames":7,"priority":"MEDIUM",
            "score":75.0,
            "first":{"time":truth[0][0],"x":670.0,"y":910.0},
            "last":{"time":truth[-1][0],"x":666.0,"y":904.0}
        }
        matched=rank_against_report([track])
        self.assertEqual(len(matched["upper_left"]["provisional_endpoint_matches"]),1)
        self.assertEqual(len(matched["lower_left"]["provisional_endpoint_matches"]),0)

    def test_time_overlap_or_matching_x_alone_does_not_validate(self):
        truth=truth_positions("upper_left")
        wrong_y={
            "id":"C03","frames":8,"priority":"HIGH",
            "score":200.0,
            "first":{"time":truth[0][0],"x":670.0,"y":300.0},
            "last":{"time":truth[-1][0],"x":666.0,"y":294.0},
        }
        results=rank_against_report([wrong_y])
        self.assertFalse(results["upper_left"]["provisional_endpoint_matches"])
        self.assertFalse(results["lower_left"]["provisional_endpoint_matches"])
        self.assertIsNone(compare_track({
            **wrong_y,"frames":2
        },truth))

if __name__=="__main__":
    unittest.main()
