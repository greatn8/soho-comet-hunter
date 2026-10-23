"""Synthetic motion vs null controls; never uses actual reports as ground truth."""
import datetime as dt
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import ccor_report_motion_nulls as tracker

UTC=dt.timezone.utc

class MotionNullsTests(unittest.TestCase):
    def test_interpolate_only_between_independent_positions(self):
        points=[("2026-10-22T23:45:00Z",1820,1718),
                ("2026-10-23T00:30:00Z",1806,1707)]
        mid=tracker.parse_time("2026-10-23T00:00:00Z")
        x,y=tracker.interpolate_report_position(points,mid)
        self.assertAlmostEqual(x,1820-14/3)
        self.assertAlmostEqual(y,1718-11/3)
        before=tracker.parse_time("2026-10-22T23:30:00Z")
        self.assertIsNone(tracker.interpolate_report_position(points,before))
    def test_timestamps_include_near_exposure_offset(self):
        start=tracker.parse_time("2026-10-23T01:00:00Z")
        points=[("2026-10-23T01:00:00Z",100,160),
                ("2026-10-23T01:15:00Z",105,165)]
        frames=[{"time":start+dt.timedelta(seconds=14),
                 "filename":"a"},{"time":start+dt.timedelta(minutes=15,seconds=14),
                 "filename":"b"}]
        rows=tracker.observations_for_case(frames,{"points":points})
        self.assertEqual(len(rows),2)
    def test_moving_source_beats_shifted_tracks(self):
        rng=np.random.default_rng(125)
        arrays=[rng.normal(0,1,(360,360)).astype(np.float64) for _ in range(7)]
        observations=[]
        for i,array in enumerate(arrays):
            x,y=130+6*i,160+3*i
            # 3x3 compact source, moving independently of static scene
            array[y-1:y+2,x-1:x+2]+=80
            moment=dt.datetime(2026,10,22,23,45,tzinfo=UTC)+dt.timedelta(minutes=15*i)
            observations.append({"frame_index":i,"time":moment.isoformat(),
                                 "file":f"frame{i}.fits","reported_xy":[x,y]})
        offsets=[(dx,dy) for dx in (-60,-40,40,60)
                          for dy in (-60,-40,40,60)]
        outcome=tracker.compare_path_against_nulls(
            arrays,observations,"raw_upper_left",offsets=offsets+[
                (-80,0),(80,0),(0,-80),(0,80)])
        self.assertEqual(outcome["status"],"RANKED_UNCALIBRATED")
        self.assertEqual(outcome["target"]["measured"],7)
        self.assertGreater(outcome["target"]["mean_center_snr"],5)
        self.assertEqual(outcome["controls_at_or_above_target"],0)
        self.assertLess(outcome["reversed_path"]["mean_center_snr"],
                        outcome["target"]["mean_center_snr"])

if __name__=="__main__":
    unittest.main()
