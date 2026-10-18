"""Tests for truthful SOHO review evidence."""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from track_evidence import audit_track_frames,position_at
from live_review import find_existing

def instant(h,m=0):
    return dt.datetime(2026,10,17,h,m,tzinfo=dt.timezone.utc)

def candidate():
    return {
        "cid":"C01","frames":9,"vx":4,"vy":0,
        "first":{"time":instant(8),"file":"20261017_0800_c3_512.jpg","x":100,"y":200},
        "last":{"time":instant(8,12),"file":"20261017_0812_c3_512.jpg","x":101,"y":200},
    }

class EvidenceTests(unittest.TestCase):
    def test_impossible_nine_frame_track_is_flagged(self):
        c=candidate()
        frames=[Path("20261017_0800_c3_512.jpg"),Path("20261017_0812_c3_512.jpg")]
        audit_track_frames(c,frames)
        self.assertEqual(c["available_source_frames"],2)
        self.assertIn("FRAME_COUNT_EXCEEDS_SOURCE_IMAGES",c["quality_issues"])

    def test_endpoint_model_uses_observed_coordinates(self):
        c=candidate()
        x,y=position_at(c,instant(8,6),"endpoints")
        self.assertAlmostEqual(x,100.5)
        self.assertEqual(y,200)

    def test_similar_velocity_does_not_reuse_wrong_clip(self):
        c=candidate()
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)/"RTOLD";folder.mkdir()
            metadata={
                "signature":{"time":"2026-10-17T08:12:00Z","x":101,"y":200,"vx":4,"vy":0},
                "first":"20261017_0700_c3_512.jpg","last":c["last"]["file"],
                "first_x":100,"first_y":200,"last_x":101,"last_y":200,
            }
            (folder/"review.json").write_text(json.dumps(metadata))
            self.assertIsNone(find_existing(Path(root),c))

if __name__=="__main__":
    unittest.main()
