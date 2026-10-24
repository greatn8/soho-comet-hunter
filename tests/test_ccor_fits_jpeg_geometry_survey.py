"""Pure tests for offline CCOR FITS/JPEG geometry survey."""
import datetime as dt
import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import ccor_fits_jpeg_geometry_survey as survey

class GeometrySurveyTests(unittest.TestCase):
 def test_pc_orientation_is_not_assumed_jpeg_transform(self):
  a=survey.orientation_degrees({"PC1_1":1,"PC2_1":0})
  b=survey.orientation_degrees({"PC1_1":0,"PC2_1":1})
  self.assertEqual(a,0.0)
  self.assertEqual(b,90.0)
  self.assertIsNone(survey.orientation_degrees({}))
 def test_timestamp_match_tolerance(self):
  utc=dt.timezone.utc
  t=dt.datetime(2026,10,22,12,30,tzinfo=utc)
  x=[{"time":t+dt.timedelta(seconds=25)}]
  self.assertEqual(survey.closest_index(t,x),0)
  self.assertIsNone(survey.closest_index(t,x,max_seconds=10))
 def test_image_stretch_preserves_shape(self):
  frame=np.arange(64,dtype=np.float32).reshape(8,8)
  image,metrics=survey.image_stretch(frame)
  self.assertEqual(image.size,(8,8))
  self.assertIsNotNone(metrics)

if __name__=="__main__":
 unittest.main()
