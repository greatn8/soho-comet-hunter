"""Synthetic boundary-masking tests: no permanent circular sky exclusion."""
import unittest
import numpy as np
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from ccor_field_rim import outer_rim_mask, track_rim_evidence
from ccor_hunter import peaks, review_shortlist

class RimTests(unittest.TestCase):
    def image(self):
        y,x=np.ogrid[:512,:512]
        arr=np.zeros((512,512),dtype=np.float32)
        inside=((x-256)**2+(y-256)**2)<205**2
        arr[inside]=36
        return arr
    def test_edge_mask_excludes_black_blue_boundary_but_not_interior(self):
        im=self.image()
        rim=outer_rim_mask(im)
        self.assertTrue(rim[110,412])
        self.assertFalse(rim[175,360])
        self.assertFalse(rim[300,300])
    def test_edge_peaks_removed_without_stopping_interior(self):
        im=self.image()
        base=self.image()
        # Bright point near outer boundary and an interior point.
        im[110,412]=240
        im[300,365]=240
        allpeaks=peaks(im,base,"ccor2",limit=70)
        self.assertTrue(any(abs(x-412)<2 and abs(y-110)<2 for x,y,_ in allpeaks))
        filtered=peaks(im,base,"ccor2",limit=70,field_rim=outer_rim_mask(im))
        self.assertFalse(any(abs(x-412)<2 and abs(y-110)<2 for x,y,_ in filtered))
        self.assertTrue(any(abs(x-365)<2 and abs(y-300)<2 for x,y,_ in filtered))
    def test_tracks_rejected_only_after_repeated_contact(self):
        im=self.image()
        mask=outer_rim_mask(im)
        tm={"a":mask,"b":mask,"c":mask}
        row={"processing_shape":[512,512],"points":[
             {"time":"a","x":412,"y":110},{"time":"b","x":413,"y":110},
             {"time":"c","x":365,"y":300}]}
        self.assertEqual(track_rim_evidence(row,tm)["status"],"BOUNDARY_CONTACT")
        row["field_rim_review"]={"status":"BOUNDARY_CONTACT"}
        row.update({"id":"bad","camera":"ccor2","first":"2026-10-25T00:00:00Z",
                    "last":"2026-10-25T01:00:00Z","frames":5,"rms_px":0.4,
                    "x_first":412,"y_first":110,"x_last":440,"y_last":100,
                    "review_validation":{"status":"PASS"}})
        self.assertEqual(review_shortlist([row]),[])
        row["points"][1]["x"]=365;row["points"][1]["y"]=300
        self.assertEqual(track_rim_evidence(row,tm)["status"],"CLEAR")
    def test_resizing_1024_keeps_geometry(self):
        im=self.image()
        scaled=np.repeat(np.repeat(im,2,axis=0),2,axis=1)
        rim=outer_rim_mask(scaled)
        self.assertTrue(rim[220,824])
        self.assertFalse(rim[600,600])

if __name__=="__main__":
    unittest.main()
