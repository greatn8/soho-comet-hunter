"""Synthetic multiframe saturated-artifact checks, no SOHO fetching."""
import datetime as dt
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from bright_artifact import neighborhood,artifact_proximity


def make_sequence(size=512,blob=True,one_frame=False):
    scale=size//512
    t0=dt.datetime(2026,10,21,12,tzinfo=dt.timezone.utc)
    images={}
    frames=[]
    for i in range(7):
        when=t0+dt.timedelta(hours=i)
        path=Path(when.strftime("%Y%m%d_%H%M")+f"_c3_{size}.jpg")
        data=bytearray([22])*(size*size)
        x=int((150+i*2)*scale)
        y=int((340-i)*scale)
        # Simulate a compact moving source, which must never itself trigger
        # a high-brightness background mask.
        for dy in range(-2*scale,2*scale+1):
            for dx in range(-2*scale,2*scale+1):
                data[(y+dy)*size+x+dx]=250
        if blob and (not one_frame or i==3):
            # Broad, solid white artifact slightly shifted every frame.
            left=x-15*scale+(i%3)*2*scale
            top=y-18*scale-(i%2)*3*scale
            for yy in range(top,top+60*scale):
                data[yy*size+left:yy*size+left+59*scale] = bytes([245])*(59*scale)
        images[path.name]=bytes(data)
        frames.append(path)
    c={"resolution":size,
       "first":{"time":t0,"x":150*scale,"y":340*scale},
       "last":{"time":t0+dt.timedelta(hours=6),
               "x":162*scale,"y":334*scale},
       "vx":2*scale,"vy":-1*scale}
    return c,frames,images


class ArtifactTests(unittest.TestCase):
    def assert_status(self,size,blob,expected,one_frame=False):
        c,frames,images=make_sequence(size,blob,one_frame)
        result=artifact_proximity(
            c,frames,{},lambda p,cache:images.get(p.name),
            lambda f:dt.datetime.strptime(f[:13],"%Y%m%d_%H%M")
                .replace(tzinfo=dt.timezone.utc))
        self.assertEqual(result["status"],expected,result)

    def test_moving_broad_artifact_512(self):
        self.assert_status(512,True,"ARTIFACT_ADJACENT")

    def test_moving_broad_artifact_1024(self):
        self.assert_status(1024,True,"ARTIFACT_ADJACENT")

    def test_isolated_compact_moving_object_512_and_1024(self):
        self.assert_status(512,False,"CLEAR")
        self.assert_status(1024,False,"CLEAR")

    def test_single_bright_frame_does_not_suppress(self):
        self.assert_status(512,True,"CLEAR",one_frame=True)

    def test_missing_frames_fail_open(self):
        c,frames,images=make_sequence()
        result=artifact_proximity(c,frames[:3],{},
                                  lambda p,cache:images.get(p.name),
                                  lambda f:dt.datetime.strptime(f[:13],"%Y%m%d_%H%M")
                                  .replace(tzinfo=dt.timezone.utc))
        self.assertEqual(result["status"],"UNAVAILABLE")

    def test_invalid_frame_buffer_fail_open(self):
        self.assertEqual(neighborhood(b"123",512,150,150)["status"],"UNAVAILABLE")


if __name__=="__main__":
    unittest.main()
