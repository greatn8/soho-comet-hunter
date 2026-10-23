"""Geometry candidates are strictly illustrative until separately calibrated."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from ccor_report_position_overlays import project,GEOMETRIES
class PositionOverlaysTests(unittest.TestCase):
    def test_all_hypotheses_in_bounds(self):
        for geometry in GEOMETRIES:
            x,y=project(1820,1718,1204,1205,geometry)
            self.assertGreaterEqual(x,0)
            self.assertLessEqual(x,1204)
            self.assertGreaterEqual(y,0)
            self.assertLessEqual(y,1205)
    def test_report_origin_flip(self):
        a=project(1024,240,1204,1205,"full_upperleft")
        b=project(1024,240,1204,1205,"full_lowerleft")
        self.assertAlmostEqual(a[0],b[0])
        self.assertAlmostEqual(a[1]+b[1],1205)
if __name__=="__main__":
    unittest.main()
