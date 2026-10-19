"""Synthetic point-source replay tests; CPU only, no SOHO or CUDA needed."""
import sys
import unittest
import datetime as dt
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from benchmark_injected_cuda import truth_positions,match_synthetic_track

class InjectedControls(unittest.TestCase):
    def test_trajectory_uses_actual_observation_times(self):
        frames=[Path("20260730_0206_c3_1024.jpg"),
                Path("20260730_0218_c3_1024.jpg"),
                Path("20260730_0230_c3_1024.jpg")]
        rows=truth_positions(frames,620,680,10,-5)
        self.assertEqual([(r["x"],r["y"]) for r in rows],
                         [(620,680),(622,679),(624,678)])

    def test_candidate_must_match_both_known_endpoints(self):
        frames=[Path(f"20260730_0{i}06_c3_1024.jpg") for i in range(2,7)]
        rows=truth_positions(frames,620,680,8,-6)
        a=dt.datetime.fromisoformat(rows[0]["utc"])
        b=dt.datetime.fromisoformat(rows[-1]["utc"])
        track={"id":"C01","frames":5,"rms":0.2,"score":100.,
               "first":{"time":a,"x":rows[0]["x"],"y":rows[0]["y"]},
               "last":{"time":b,"x":rows[-1]["x"],"y":rows[-1]["y"]}}
        self.assertEqual(match_synthetic_track(track,rows)["id"],"C01")
        track["last"]["x"]+=20
        self.assertIsNone(match_synthetic_track(track,rows))

    def test_same_time_track_cannot_pass(self):
        frames=[Path("20260730_0206_c3_1024.jpg")]
        rows=truth_positions(frames,620,680,8,-6)
        a=dt.datetime.fromisoformat(rows[0]["utc"])
        track={"id":"C01","frames":7,"rms":0.2,"score":100.,
               "first":{"time":a,"x":620,"y":680},
               "last":{"time":a,"x":620,"y":680}}
        self.assertIsNone(match_synthetic_track(track,rows))

if __name__=="__main__":
    unittest.main()
