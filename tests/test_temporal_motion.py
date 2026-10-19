"""Temporal motion verifier regression tests with synthetic SOHO-size frames."""
import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from pixel_verify import verify_temporal_motion

class TemporalMotionTests(unittest.TestCase):
    def make_frames(self, moving):
        frames=[]
        images={}
        for i in range(12):
            name=f"20261019_{i:02d}00_c3_512.jpg"
            f=Path(name)
            frames.append(f)
            pixels=bytearray([20])*(512*512)
            x=100+5*i if moving else 125
            y=225
            for dy in (-1,0,1):
                for dx in (-1,0,1):
                    pixels[(y+dy)*512+x+dx]=230
            images[str(f)]=bytes(pixels)
        return frames,images

    def test_moving_source_beats_stationary_control(self):
        t=dt.datetime(2026,10,19,tzinfo=dt.timezone.utc)
        c={"first":{"time":t,"x":100,"y":225},
           "last":{"time":t+dt.timedelta(hours=11),"x":155,"y":225},
           "vx":5,"vy":0}
        with patch("pixel_verify._decode_gray",side_effect=lambda f,cache: self.images[str(f)]):
            frames,self.images=self.make_frames(True)
            positive=verify_temporal_motion(c,frames)
            frames,self.images=self.make_frames(False)
            negative=verify_temporal_motion(c,frames)
        self.assertEqual(positive["status"],"MEASURED")
        self.assertGreater(positive["supported"],negative["supported"])
        self.assertGreater(positive["support_fraction"],negative["support_fraction"])

if __name__=="__main__":
    unittest.main()
