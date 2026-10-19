"""Offline spatial CUDA replay geometry regression tests."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from benchmark_spatial_cuda import region_bounds,mask_filter,summarize_tracks,run_cuda_region
from unittest.mock import patch
import tempfile

class SpatialReplayTests(unittest.TestCase):
    def test_overlapping_regions_preserve_original_coordinates(self):
        self.assertEqual(region_bounds(1024,4,0,48),(0,0,304,304))
        self.assertEqual(region_bounds(1024,4,15,48),(720,720,1024,1024))
        self.assertEqual(region_bounds(1024,4,6,48),(464,208,816,560))
    def test_mask_filter_blacks_only_outside(self):
        vf=mask_filter(1024,(464,208,816,560))
        self.assertIn("drawbox=x=0:y=0:w=1024:h=208",vf)
        self.assertIn("drawbox=x=0:y=208:w=464:h=352",vf)
    def test_static_control_uses_same_source_for_every_timestamp(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            files=[root/f"20260730_0{i}06_c3_1024.jpg" for i in range(5)]
            for f in files: f.write_bytes(b"input")
            calls=[]
            def fake_mask(source,dest,size,bounds):
                calls.append((source,dest))
            proc=MagicMock(returncode=0,stdout="",stderr="")
            with patch("benchmark_spatial_cuda.encode_masked",side_effect=fake_mask), \
                 patch("benchmark_spatial_cuda.subprocess.run",return_value=proc):
                status,rows=run_cuda_region(Path("/bin/true"),root,files,1024,
                                            (0,0,304,304),1,250,root/"run.log",
                                            control="static")
            self.assertEqual(status,0)
            self.assertEqual(len(calls),5)
            self.assertEqual({src for src,_ in calls},{files[0]})
            self.assertEqual({dst.name for _,dst in calls},{f.name for f in files})

    def test_no_track_is_verified_just_for_roi_membership(self):
        result=summarize_tracks([],(0,0,256,256),23,250)
        self.assertEqual(result["midpoints_inside_roi"],0)
        self.assertEqual(result["good_fit_rms_le_1_2"],0)
        self.assertFalse(result["cap_reached"])

if __name__=="__main__":unittest.main()
