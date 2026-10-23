"""Pure tests for full-frame CCOR registration diagnostic."""
import sys, unittest
from pathlib import Path
import numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import ccor_fullframe_registration_audit as audit

class CCORFullFrameAuditTests(unittest.TestCase):
    def test_black_region_is_flagged(self):
        frame=Image.new("RGB",(1204,1205),(0,0,0))
        metrics=audit.local_pixel_metrics(frame,850,850)
        self.assertEqual(metrics["local_fraction_black_rgb_lt_4"],1.0)
        self.assertEqual(metrics["local_rgb_mean"],0)
    def test_bright_region_is_not_black(self):
        frame=Image.new("RGB",(1204,1205),(50,60,70))
        metrics=audit.local_pixel_metrics(frame,850,850)
        self.assertEqual(metrics["local_fraction_black_rgb_lt_4"],0)
    def test_annotated_geometries_differ_and_preserve_original(self):
        frame=Image.new("RGB",(1204,1205),(35,45,55))
        result,metrics=audit.annotate_full(frame,(1530,1208),"test")
        self.assertEqual(result.size,frame.size)
        self.assertEqual(frame.getpixel((0,0)),(35,45,55))
        self.assertEqual(set(metrics),set(audit.GEOMETRIES))
        self.assertNotEqual(metrics["full_upperleft"]["jpeg_xy"],
                            metrics["full_lowerleft"]["jpeg_xy"])
    def test_temporal_variation_map(self):
        a=Image.new("RGB",(100,100),(5,5,5))
        b=Image.new("RGB",(100,100),(50,50,50))
        out=audit.temporal_map([a,b])
        self.assertEqual(out.size,(100,100))
if __name__=="__main__":
    unittest.main()
