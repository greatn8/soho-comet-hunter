"""Synthetic controls for offline C3 persistent dark-streak diagnostic."""
import sys
from pathlib import Path
import unittest

try:
    import numpy as np
except ImportError:
    raise unittest.SkipTest("numpy required; independent shadow diagnostic workflow installs it")

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from diagnose_c3_shadow_streak import (
    shadow_masks, track_path_fraction, contact_summary, latest_complete_logs,
    choose_evenly, SIZE
)


class ShadowStreakTests(unittest.TestCase):
    def make_frames(self):
        # Persistent dark vertical streak in lower-left, with transient
        # bright comet dots moving horizontally across 15 exposures.
        frames=[]
        for i in range(15):
            a=np.full((SIZE,SIZE),110,dtype=np.uint8)
            a[320:450,90:100]=35
            a[365,155+i*2]=230
            frames.append(a)
        return np.stack(frames)

    def test_dark_streak_detected_but_moving_source_not_stamped(self):
        shadow,edge,median=shadow_masks(self.make_frames())
        self.assertTrue(shadow[380,95])
        self.assertFalse(shadow[380,155])
        self.assertFalse(shadow[150,95])
        self.assertFalse(shadow[15,95])
        self.assertTrue(np.any(edge[340:440,86:105]))
        self.assertEqual(median.shape,(512,512))

    def test_artifact_paths_exceed_shifted_controls(self):
        shadow,edge,_=shadow_masks(self.make_frames())
        artifact=shadow|edge
        track={"first":{"x":95,"y":345},"last":{"x":95,"y":425}}
        score=track_path_fraction(track,artifact,quadrant="LOWER_LEFT")
        self.assertGreater(score,0.8)
        result=contact_summary([track],artifact,"LOWER_LEFT")
        self.assertEqual(result["paths_compared"],1)
        self.assertGreater(result["contact_excess_percentage_points"],30)

    def test_known_comet_like_moving_path_not_blacked_out(self):
        shadow,edge,_=shadow_masks(self.make_frames())
        moving={"first":{"x":155,"y":365},"last":{"x":183,"y":365}}
        self.assertEqual(track_path_fraction(moving,shadow|edge,quadrant="LOWER_LEFT"),0.0)

    def test_frame_selection_no_repeat(self):
        self.assertEqual(choose_evenly(list(range(10)),3),[0,4,9])
        self.assertEqual(choose_evenly([1,2],15),[1,2])


if __name__=="__main__":
    unittest.main()
