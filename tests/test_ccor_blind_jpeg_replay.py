"""Offline tests for position-blind actual CCOR detector replay and registration gate."""
import sys,unittest,datetime as dt,tempfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import ccor_blind_jpeg_replay as replay
import ccor_evaluate_blind_jpeg as evaluate

class BlindReplayTests(unittest.TestCase):
    def test_detector_fingerprint_and_no_report_positions(self):
        self.assertEqual(len(replay.detector_fingerprint()),64)
        self.assertNotIn("points",replay.CASES["ccor2-20261022-wage"])
    def test_replay_actual_detector_with_synthetic_jpegs(self):
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td)
            images=folder/"ccor2-20261022-wage"/"jpegs"
            images.mkdir(parents=True)
            for i in range(8):
                stamp=(dt.datetime(2026,10,22,23,0)+dt.timedelta(minutes=15*i)).strftime("%Y%m%d_%H%M")
                img=Image.new("RGB",(600,600),(10,10,10))
                img.save(images/f"{stamp}_ccor2_600by600.jpg",quality=95)
            with patch.object(replay.hunter,"track_points", wraps=replay.hunter.track_points) as m:
                row=replay.blind_detect("ccor2-20261022-wage",folder,no_download=True)
            self.assertEqual(row["status"],"POSITION_BLIND_REPLAY_COMPLETE")
            self.assertEqual(row["frame_count"],8)
            self.assertTrue(m.called)
            self.assertEqual(row["track_count"],0)
            self.assertEqual(row["reported_comet_recovered"],None)
    def test_calibration_requires_heldout_and_span(self):
        with self.assertRaises(ValueError):
            evaluate.fit_affine_from_landmarks([
                {"role":"train","native_xy":[0,0],"jpeg_xy":[0,0]}])
    def test_independent_landmark_validation(self):
        original=[(0,0),(100,0),(0,100),(100,100)]
        heldout=[(25,30),(80,60),(30,80)]
        landmark=[]
        for kind,points in (("train",original),("validation",heldout)):
            for x,y in points:
                landmark.append({"role":kind,"native_xy":[x,y],
                                 "jpeg_xy":[2*x+1,3*y+4]})
        transform,check=evaluate.fit_affine_from_landmarks(landmark)
        self.assertLess(check["validation_rms_pixels"],1e-9)
        self.assertTrue(np.allclose(np.array([25,30,1])@transform,[51,94]))
if __name__=="__main__":unittest.main()
