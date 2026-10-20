"""Offline tests using a small synthetic ZIP with independently labelled C3 truth."""
import sys
from pathlib import Path
import tempfile
import unittest
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from benchmark_official_practice_c3 import parse_positions,identify_c3_training,match_comet

HEADER="""Notes: x and y coordinates assume (0,0) is the upper-left corner of the images.
all images are assumed to be LASCO C3 unless stated "[c2]"
DATE TIME x y (alt. y) [c2]
"""

class PracticeCometTests(unittest.TestCase):
    def test_c2_is_excluded(self):
        rows=parse_positions(HEADER+"""2008/11/06 07:54:04 689 1017 (7) [c2]
2008/11/06 08:06:04 682 1007 (17) [c2]""")
        self.assertEqual([r["camera"] for r in rows],["c2","c2"])

    def test_c3_is_included_without_c2(self):
        data=HEADER+"""2008/11/06 07:54:04 689 1017 (7) [c2]
2008/11/06 08:06:04 682 1007 (17)
2008/11/06 08:30:04 667 986 (38)
2008/11/06 08:54:04 653 958 (66)
2008/11/06 09:06:04 646 947 (77)
2008/11/06 09:54:04 633 919 (105)
"""
        with tempfile.TemporaryDirectory() as temp:
            archive=Path(temp)/"soho9999.zip"
            with zipfile.ZipFile(archive,"w") as z:
                z.writestr("soho9999/soho9999_positions.txt",data)
                for t in ("0754","0806","0830","0854","0906","0954"):
                    z.writestr(f"soho9999/full_soho9999_081106_{t}.gif",b"GIF89a")
            case=identify_c3_training(archive)
            self.assertEqual(case["positions_c2"],1)
            self.assertEqual(case["positions_c3"],5)
            self.assertEqual(case["matched_c3_gifs"],5)

    def test_comet_path_vs_false_track(self):
        import datetime as dt
        t=dt.datetime(2008,11,6,8,0,tzinfo=dt.timezone.utc)
        truth=[{"time":t+dt.timedelta(minutes=12*i),"x":700-8*i,"y":800-10*i} for i in range(7)]
        good={"id":"C01","frames":7,
              "first":{"time":truth[0]["time"],"x":700,"y":800},
              "last":{"time":truth[-1]["time"],"x":652,"y":740}}
        bad={"id":"C02","frames":7,
             "first":{"time":truth[0]["time"],"x":900,"y":200},
             "last":{"time":truth[-1]["time"],"x":800,"y":300}}
        self.assertTrue(match_comet([good,bad],truth)["comet_recovered"])
        self.assertFalse(match_comet([bad],truth)["comet_recovered"])

if __name__=="__main__":
    unittest.main()
