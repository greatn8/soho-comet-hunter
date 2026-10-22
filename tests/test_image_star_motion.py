"""Fast synthetic checks for independent sky-motion comparison."""
import unittest,sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from image_star_motion import extract_compact_sources,evidence_for_velocity,estimate_field_motion


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


    def test_time_aligned_frame_pair(self):
        import datetime as dt
        from pathlib import Path
        from image_star_motion import build_reference
        base=dt.datetime(2026,10,22,0,tzinfo=dt.timezone.utc)
        frames=[Path((base+dt.timedelta(hours=h)).strftime(
            "%Y%m%d_%H%M")+"_c3_512.jpg") for h in range(10)]
        g1,g2=images(512)
        def decode(path,cache):
            return g1 if path.name.startswith("20261022_02") else g2
        def when(name):
            return dt.datetime.strptime(name[:13],"%Y%m%d_%H%M").replace(
                tzinfo=dt.timezone.utc)
        ref=build_reference(frames,{},decode,512,when,target_hours=2,
            end_frame=frames[4].name)
        self.assertEqual(ref["status"],"READY")
        self.assertEqual(ref["first_file"],frames[2].name)
        self.assertEqual(ref["last_file"],frames[4].name)
        missing=build_reference(frames,{},decode,512,when,
            end_frame="20261021_2230_c3_512.jpg")
        self.assertEqual(missing["reason"],"END_FRAME_NOT_CACHED")

    def test_independent_motion_estimate(self):
        import math
        for resolution in (512,1024):
            a,b=images(resolution)
            first=extract_compact_sources(a,resolution)
            last=extract_compact_sources(b,resolution)
            from collections import defaultdict
            scale=resolution/512
            cell=8*scale
            index=defaultdict(list)
            for x,y,c in last:
                index[(int(x//cell),int(y//cell))].append((x,y))
            reference={"status":"READY","hours":4,"resolution":resolution,
                       "sources":first,"index":index,"cell":cell}
            estimated=estimate_field_motion(reference)
            self.assertEqual(estimated["status"],"READY")
            candidates=estimated["hypotheses"]
            self.assertTrue(any(math.hypot(m["vx_512"]-1.75,
                m["vy_512"]+0.75)<0.4 for m in candidates),candidates)

    def test_missing_reference_does_not_reject(self):
        self.assertEqual(evidence_for_velocity(
            {"vx":3,"vy":-1},{"status":"UNAVAILABLE","reason":"NO_FRAMES"})
            ["status"],"UNAVAILABLE")


if __name__=="__main__":
    unittest.main()
