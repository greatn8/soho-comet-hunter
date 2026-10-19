"""Offline benchmark must never confuse temporal overlap with discovery recall."""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from benchmark_known_comets import controls, evaluate, parse_output, pick_frames, when, index_images

class ReplayTests(unittest.TestCase):
    def test_full_workspace_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            nested=root/"old_archive"/"cache"/"2024"
            nested.mkdir(parents=True)
            (nested/"20241126_0354_c3_1024.jpg").write_bytes(b"test")
            (nested/"possible.tar.gz").write_bytes(b"test")
            indexed,n,per_root,archives=index_images([root])
            self.assertEqual(n,2)
            self.assertIn(when("20241126_0354_c3_1024.jpg"),indexed[1024])
            self.assertEqual(per_root[str(root)][1024],1)
            self.assertTrue(any(x.endswith("possible.tar.gz") for x in archives))

    def test_naive_soho_times_are_utc_not_host_local(self):
        import os
        import time
        old = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "Australia/Sydney"
            if hasattr(time,"tzset"):
                time.tzset()
            self.assertEqual(when("2026-07-30T02:30:00").isoformat(),
                             "2026-07-30T02:30:00+00:00")
            self.assertEqual(when("2026-07-30T13:30:00+11:00").isoformat(),
                             "2026-07-30T02:30:00+00:00")
            case = next(x for x in controls(Path(__file__).resolve().parents[1]/
                             "docs/data/candidates.json")
                        if x["event_id"] == "EV20260730T0530_1E9C7C")
            self.assertEqual(case["first"].date().isoformat(),"2026-07-30")
            self.assertEqual(case["first"].hour,2)
        finally:
            if old is None:
                os.environ.pop("TZ",None)
            else:
                os.environ["TZ"]=old
            if hasattr(time,"tzset"):
                time.tzset()

    def test_known_controls_loaded(self):
        path=Path(__file__).resolve().parents[1]/"docs/data/candidates.json"
        known=controls(path)
        self.assertGreaterEqual(len(known),5)
        self.assertTrue(any("SOHO-5143" in x["known_object"] for x in known))

    def test_temporal_overlap_does_not_count_as_recovery(self):
        event={"first":when("2026-07-30T02:30:00"),
               "last":when("2026-07-30T05:30:00")}
        track={"id":"C1","frames":7,"first":{"time":when("2026-07-30T03:00:00"),"x":10,"y":10},
               "last":{"time":when("2026-07-30T05:00:00"),"x":12,"y":12}}
        answer=evaluate([track],event,1024)
        self.assertEqual(answer["temporal_hypotheses"],1)
        self.assertIsNone(answer["positional_recovered"])
        self.assertEqual(answer["interpretation"],"temporal_only_unverified")

    def test_independent_reference_points_can_match(self):
        event={"first":when("2026-07-30T02:30:00"),
               "last":when("2026-07-30T05:30:00")}
        track={"id":"C1","frames":7,"first":{"time":when("2026-07-30T03:00:00"),"x":10,"y":20},
               "last":{"time":when("2026-07-30T05:00:00"),"x":14,"y":24}}
        truth=[(when("2026-07-30T03:00:00"),20,40),
               (when("2026-07-30T04:00:00"),24,44),
               (when("2026-07-30T05:00:00"),28,48)]
        result=evaluate([track],event,512,truth)
        self.assertTrue(result["positional_recovered"])
        self.assertEqual(result["matched_candidates"],["C1"])

    def test_parse_and_select_original_resolution(self):
        output=("C7 [MEDIUM] frames=7 speed=5.0 px/h vx=1.0 vy=1.0 "
                "RMS=0.45 brightnessCV=0.2 sunward=3.0 px/h score=110.0\n"
                " first: 20260730_0230_c3_1024.jpg (100.0, 200.0)\n"
                " last: 20260730_0530_c3_1024.jpg (110.0, 210.0)\n")
        tracks=parse_output(output)
        self.assertEqual(tracks[0]["frames"],7)
        self.assertEqual(tracks[0]["first"]["x"],100)
        available={when("20260730_0230_c3_1024.jpg"):
                   [Path("20260730_0230_c3_1024.jpg")]}
        selected=pick_frames(available,when("2026-07-30T02:30:00"),
                             when("2026-07-30T05:30:00"))
        self.assertEqual(len(selected),1)

if __name__=="__main__":
    unittest.main()
