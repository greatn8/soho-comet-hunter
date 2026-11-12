"""Source-provenance and native-coordinate reporting validation."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import ccor_native_report_kit as kit
from PIL import Image
import datetime as dt

class NativeReportTests(unittest.TestCase):
 def test_exact_geometry_and_valid_png(self):
  yy,xx=np.ogrid[:1920,:2048]
  arr=((xx+yy)/1000.0).astype(float)
  arr[800,1300]=100.0
  im=kit.native_preview(arr)
  self.assertEqual(im.size,(2048,1920))
  self.assertGreater(im.getpixel((1300,800)),im.getpixel((200,200)))
 def test_native_points_preserve_zero_based_indices_and_hashes(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);fits=root/"fits";fits.mkdir()
   out=root/"out";paths=[]
   for i in range(3):
    p=fits/(f"CCOR1_1B_20261022T12{i*15:02d}25_V00_NC.fits")
    p.write_bytes(f"FITS-TEST-{i}".encode());paths.append(p)
   yy,xx=np.ogrid[:1920,:2048]
   arr=((xx+yy)/1000.0).astype(float);arr[100:150,100:150]=50.0
   def fake_load(path):
    i=paths.index(path)
    return {"array":arr,"camera":"ccor1","timestamp":dt.datetime(2026,10,22,12,i*15,25,736000,tzinfo=dt.timezone.utc),
            "processing_level":"1B","shape":[1920,2048],"wcs":{},"time_source":"filename"}
   with patch.object(kit,"load_fits",side_effect=fake_load):
    manifest=kit.prepare(fits,out)
   points={"camera":"ccor1","observations":[
      {"source_file":ref["source_file"],"preview_sha256":ref["preview_sha256"],
       "x":1200+i*4,"y":700+i*5} for i,ref in enumerate(manifest["sources"])]}
   ref=out/"measured.json";ref.write_text(json.dumps(points))
   result=kit.export(fits,out,ref)
   self.assertFalse(result["ready_to_submit"])
   self.assertIn("25.736000Z",result["observations"][0]["time_utc"])
   self.assertEqual(result["observations"][0]["fits_pixel_1based"],[1201,701])
   self.assertTrue((out/"report_positions.json").exists())
   with self.assertRaises(FileExistsError):kit.export(fits,out,ref)
 def test_faint_field_survives_bright_occulting_edge(self):
  arr=np.full((1920,2048),2e-10)
  arr[100:200,:]=1e-4
  arr[1000,1500]=2.1e-10
  im=kit.native_preview(arr)
  self.assertEqual(np.asarray(im).dtype,np.uint16)
  self.assertGreater(im.getpixel((1500,1000))-im.getpixel((1499,1000)),10)
 def test_fractional_seconds_preserved(self):
  t=dt.datetime(2026,10,22,12,30,25,736000,tzinfo=dt.timezone.utc)
  self.assertEqual(kit.iso(t),"2026-10-22T12:30:25.736000Z")
 def test_never_accepts_invalid_pixel_index(self):
  with self.assertRaises(ValueError):kit._ensure_int(2048,"x",2048)
  with self.assertRaises(ValueError):kit._ensure_int(12.5,"y",1920)
  with self.assertRaises(ValueError):kit._ensure_int(-1,"x",2048)
if __name__=="__main__":unittest.main()

