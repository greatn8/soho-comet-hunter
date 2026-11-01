import importlib.util
from pathlib import Path
import tempfile
import unittest
import numpy as np

spec=importlib.util.spec_from_file_location('reference',Path(__file__).parents[1]/'tools/build_ccor_confirmed_reference.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class ReferenceTests(unittest.TestCase):
 def test_shared_stretch_preserves_identical_frame_values(self):
  y,x=np.mgrid[:40,:50];base=1+(x+y)/100
  a=np.stack([base.copy() for _ in range(6)]).astype('float32')
  for i in range(6):a[i,10+i,10+i]+=2
  original,temporal,settings=m.render(a)
  self.assertTrue(settings['same_stretch_all_frames'])
  self.assertEqual(len(set(int(frame[5,5]) for frame in original)),1)
  self.assertEqual(len(set(int(frame[5,5]) for frame in temporal)),1)
  self.assertGreater(int(temporal[0,10,10]),int(temporal[1,10,10]))
 def test_no_false_recovery_or_native_pixel_claim(self):
  import json
  from PIL import Image
  folder=Path(__file__).parents[1]/'docs/data/ccor-reference'
  result=json.loads((folder/'manifest.json').read_text())
  self.assertIsNone(result['live_detector_recovery'])
  self.assertFalse(result['production_modified'])
  self.assertEqual(result['target_location_status'],'UNREGISTERED_OFFICIAL_DEMONSTRATION_TO_NATIVE_ARRAY')
  self.assertEqual(result['frame_count'],17)
  self.assertAlmostEqual(result['elapsed_hours'],4,places=4)
  for frame in result['frames']:
   self.assertEqual(len(frame['source_sha256']),64)
   for mode in ['original','temporal']:
    with Image.open(folder/frame[mode]) as im:self.assertEqual(im.size,(1024,960));im.verify()
 def test_duplicate_times_fail(self):
  row={'time_utc':'2025-02-11T07:00:20Z'}
  with tempfile.TemporaryDirectory() as td:
   with self.assertRaisesRegex(ValueError,'Duplicate'):m.build([row]*6,Path(td))
 def test_no_temporal_information_fails(self):
  with self.assertRaises(ValueError):m.render(np.ones((6,40,50)))

if __name__=='__main__':unittest.main()
