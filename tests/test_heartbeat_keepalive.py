"""Heartbeat remains fresh during long work without implying a completed frame."""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from realtime_hunter import pulse_heartbeat, write_heartbeat


class HeartbeatTests(unittest.TestCase):
    def test_pulse_preserves_status_progress_and_last_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"last_detector_frame.txt").write_text("20261016_1442_c3_512.jpg")
            write_heartbeat(root,status="running",phase="CUDA processing",
                            progress={"window":48},new_frames=4)
            original=json.loads((root/"heartbeat.json").read_text())
            old=dict(original,checked_at="2026-10-15T00:00:00Z")
            (root/"heartbeat.json").write_text(json.dumps(old))
            pulse_heartbeat(root)
            current=json.loads((root/"heartbeat.json").read_text())
            self.assertNotEqual(current["checked_at"],old["checked_at"])
            for key in ("status","phase","progress","new_frames",
                        "last_frame","phase_started_at"):
                self.assertEqual(current[key],old[key])

    def test_error_state_not_masked_by_keepalive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_heartbeat(root,status="error",error="CUDA failed")
            old=json.loads((root/"heartbeat.json").read_text())
            pulse_heartbeat(root)
            current=json.loads((root/"heartbeat.json").read_text())
            self.assertEqual(current,old)


if __name__=="__main__":
    unittest.main()
