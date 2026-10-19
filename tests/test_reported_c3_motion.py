"""Unit tests for temporal-background matched-path scoring without SOHO data."""
import functools
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from score_reported_c3_motion import evaluate_path

class MotionPathTests(unittest.TestCase):
    def setUp(self):
        self.size=64
        self.anchor_frames=[1,3,5,7,9]
        self.points=[(20+4*i,30) for i in range(5)]

    def make_frames(self,move):
        frames=[bytearray([10])*(self.size*self.size) for _ in range(13)]
        for frame in frames:
            # Stationary high-contrast star never constitutes motion.
            for dy in (-1,0,1):
                for dx in (-1,0,1):
                    frame[(48+dy)*self.size+(48+dx)]=250
        if move:
            for frame_index,(x,y) in zip(self.anchor_frames,self.points):
                for dy in (-1,0,1):
                    for dx in (-1,0,1):
                        frames[frame_index][(y+dy)*self.size+x+dx]=210
        return frames

    def median(self,frames):
        @functools.lru_cache(maxsize=None)
        def med(pixel_index):
            return sorted(f[pixel_index] for f in frames)[len(frames)//2]
        return med

    def test_static_scene_has_no_temporal_signal(self):
        frames=self.make_frames(False)
        result=evaluate_path(frames,self.anchor_frames,self.points,
                             self.size,self.median(frames))
        self.assertEqual(result["coherent_score"],0)
        self.assertEqual(result["positive_frame_count"],0)

    def test_injected_moving_source_stays_aligned(self):
        frames=self.make_frames(True)
        result=evaluate_path(frames,self.anchor_frames,self.points,
                             self.size,self.median(frames))
        self.assertGreater(result["coherent_score"],100)
        self.assertEqual(result["strong_positive_frame_count"],5)

if __name__=="__main__":
    unittest.main()
