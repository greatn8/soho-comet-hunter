"""Fast synthetic checks for independent sky-motion comparison."""
import unittest,sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from image_star_motion import extract_compact_sources,evidence_for_velocity


def images(res):
    scale=res/512
    stars=[(int(x*scale),int(y*scale))
           for x in range(60,480,70) for y in range(60,480,70)]
    g1=bytearray([25])*(res*res)
    g2=bytearray([25])*(res*res)
    for x,y in stars:
        g1[y*res+x]=210
        g2[(y-int(3*scale))*res+x+int(7*scale)]=210
    return bytes(g1),bytes(g2)


class ImageStarTests(unittest.TestCase):
    def test_matches_real_motion_at_both_resolutions(self):
        for resolution in (512,1024):
            a,b=images(resolution)
            first=extract_compact_sources(a,resolution)
            second=extract_compact_sources(b,resolution)
            self.assertGreaterEqual(len(first),20)
            scale=resolution/512
            cell=8*scale
            index=defaultdict(list)
            for x,y,c in second:
                index[(int(x//cell),int(y//cell))].append((x,y))
            ref={"status":"READY","hours":4,"resolution":resolution,
                 "sources":first,"index":index,"cell":cell}
            real=evidence_for_velocity(
                {"vx":7/4*scale,"vy":-3/4*scale},ref)
            other=evidence_for_velocity(
                {"vx":-3*scale,"vy":-2*scale},ref)
            self.assertEqual(real["status"],"STAR_LIKE")
            self.assertNotEqual(other["status"],"STAR_LIKE")

    def test_missing_reference_does_not_reject(self):
        self.assertEqual(evidence_for_velocity(
            {"vx":3,"vy":-1},{"status":"UNAVAILABLE","reason":"NO_FRAMES"})
            ["status"],"UNAVAILABLE")


if __name__=="__main__":
    unittest.main()
