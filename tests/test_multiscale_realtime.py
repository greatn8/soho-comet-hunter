"""Regression checks for independent multiscale searches using cached frames."""
import datetime as dt
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from realtime_hunter import run_multiscale_cuda, select_recent_frames

class MultiscaleTests(unittest.TestCase):
    def test_windows_select_recent_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            frames=[root/f"20261016_{hh:02d}00_c3_512.jpg" for hh in (0,10,20,23)]
            self.assertEqual([p.name for p in select_recent_frames(frames,12)],
                             [frames[2].name,frames[3].name])
            self.assertEqual(len(select_recent_frames(frames,48)),4)

    def test_experimental_filter_marker_survives_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); cache=root/"cache"; cache.mkdir()
            (root/"state").mkdir()
            (root/"state"/"realtime_cuda_stationary_enabled").write_text("on\\n")
            for hour in range(5):
                (cache/f"20261016_{hour:02d}00_c3_512.jpg").write_bytes(b"fake")
            calls=[]
            def fake_run(cmd,**kwargs):
                calls.append(cmd)
                return types.SimpleNamespace(returncode=0,stdout="")
            args=types.SimpleNamespace(search_windows=(2,5,12),
                short_budget_minutes=1,medium_budget_minutes=1,
                compute_minutes=1,min_frames=2,max_candidates=100)
            with patch("realtime_hunter.subprocess.run",side_effect=fake_run):
                _,stats=run_multiscale_cuda(
                    root,sorted(cache.glob("*.jpg")),root,args,"test",cache)
            self.assertEqual(len(calls),3)
            self.assertTrue(all("--within-window-stationary-filter" in cmd for cmd in calls))
            self.assertTrue(all(row["stationary_filter_enabled"] for row in stats))

    def test_three_independent_runs_and_namespaced_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); cache=root/"cache"; cache.mkdir()
            for hour in (0,1,2,3,4,5,6,7):
                (cache/f"20261016_{hour:02d}00_c3_512.jpg").write_bytes(b"fake")
            sample=(
                "C01 [HIGH] frames=5 speed=4.00 px/h vx=1.00 vy=1.00 "
                "RMS=0.20 brightnessCV=0.10 sunward=3.00 px/h score=90.0\n"
                " first: 20261016_0200_c3_512.jpg (100.0, 100.0)\n"
                " last: 20261016_0700_c3_512.jpg (105.0, 105.0)\n"
            )
            calls=[]
            def fake_run(cmd,**kwargs):
                stage=Path(cmd[cmd.index("--input-dir")+1])
                calls.append((len(list(stage.glob("*.jpg"))),tuple(cmd)))
                return types.SimpleNamespace(returncode=0,stdout=sample)
            args=types.SimpleNamespace(search_windows=(2,5,12),
                  short_budget_minutes=1,medium_budget_minutes=1,
                  compute_minutes=1,min_frames=2,max_candidates=100)
            with patch("realtime_hunter.subprocess.run",side_effect=fake_run):
                rows,stats=run_multiscale_cuda(
                    root,sorted(cache.glob("*.jpg")),root,args,"test",cache)
            self.assertEqual([c["cid"] for c in rows],
                             ["C0020001","C0050001","C0120001"])
            self.assertEqual([n for n,_ in calls],[3,6,8])
            self.assertEqual(len(stats),3)
            self.assertTrue(all(s["status"]=="OK" for s in stats))

if __name__=="__main__":
    unittest.main()
