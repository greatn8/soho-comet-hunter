"""Official C3 position control and benchmark log checks."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from official_c3_positive_control import control,truth_points
from inspect_known_comet_tracks import inspect

class OfficialControlTests(unittest.TestCase):
    def test_position_origin_conversions(self):
        self.assertEqual(control()["event_id"],"OFFICIAL_C3_20260728_WB")
        self.assertEqual([x[2] for x in truth_points("lower_left")],[234,235,236])
        self.assertEqual([x[2] for x in truth_points("upper_left")],[790,789,788])
    def test_log_parser_and_saturation(self):
        text=("C1 [MEDIUM] | pass=deep-01 | frames=7 | speed=1.0 px/h | "
              "vx=0.0 | vy=1.0 | RMS=0.30 | brightnessCV=0.2 | "
              "sunward=1.0 px/h | score=100.0\n"
              " first: 20260730_0206_c3_1024.jpg (100.0, 100.0)\n"
              " last : 20260730_0306_c3_1024.jpg (101.0, 101.0)\n")
        r=inspect(text)
        self.assertEqual(r["parsed_tracks"],1)
        self.assertEqual(r["parse_loss"],0)
        self.assertFalse(r["candidate_cap_hit"])

if __name__=="__main__": unittest.main()
