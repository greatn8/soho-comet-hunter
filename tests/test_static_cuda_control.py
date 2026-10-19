"""Unmasked negative-control tests that do not invoke the GPU."""
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from benchmark_static_cuda import stage_images,sha256,summarize

class StaticControlTests(unittest.TestCase):
    def test_static_frames_have_identical_bytes_and_distinct_filenames(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            inputs=root/"original"; inputs.mkdir()
            stage=root/"stage";stage.mkdir()
            files=[]
            for i in range(5):
                f=inputs/f"20260730_0{i}06_c3_1024.jpg"
                f.write_bytes(f"source-frame-{i}".encode())
                files.append(f)
            staged=stage_images(files,stage,"static")
            self.assertEqual(len(staged),5)
            self.assertEqual(len({p.name for p in staged}),5)
            self.assertEqual(len({sha256(p) for p in staged}),1)
            self.assertEqual([x.read_bytes() for x in files],
                             [f"source-frame-{i}".encode() for i in range(5)])
    def test_identical_images_moving_track_must_be_false(self):
        import datetime as dt
        a=dt.datetime(2026,7,30,2,tzinfo=dt.timezone.utc)
        b=a+dt.timedelta(hours=1)
        track={"first":{"time":a,"x":100,"y":100},
               "last":{"time":b,"x":150,"y":150},
               "frames":5,"rms":0.3}
        result=summarize("",[track],5,250,"static")
        self.assertEqual(result["definitely_false_moving_tracks"],1)
        self.assertEqual(result["interpretation"],"NO_TRUE_MOTION_POSSIBLE")

if __name__=="__main__":unittest.main()
