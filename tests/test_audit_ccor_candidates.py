"""Read-only CCOR audit coordinate and duplicate checks."""
import pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"tools"))
from audit_ccor_candidates import point,overlap

class AuditTests(unittest.TestCase):
    def base(self,ident,x,y,vx=3,vy=-1,camera="ccor1"):
        return {"id":ident,"camera":camera,"first":"2026-10-24T10:00:00Z",
                "last":"2026-10-24T13:00:00Z",
                "x_first":x,"y_first":y,"vx_px_h":vx,"vy_px_h":vy}
    def test_predict_by_elapsed_hours(self):
        a=self.base("A",10,50)
        self.assertEqual(point(a,"2026-10-24T12:00:00Z"),(16,48))
    def test_close_same_camera_tracks_flagged(self):
        a=self.base("A",10,50)
        b=self.base("B",12,51)
        self.assertIsNotNone(overlap(a,b))
    def test_different_moving_objects_not_conflated(self):
        self.assertIsNone(overlap(self.base("A",10,50),self.base("B",10,50,15,-1)))
    def test_two_cameras_not_conflated(self):
        self.assertIsNone(overlap(self.base("A",10,50),self.base("B",10,50,camera="ccor2")))
if __name__=="__main__":
    unittest.main()
