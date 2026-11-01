"""Image-level injection controls for bounded CCOR seed baselines."""
import datetime as dt
from pathlib import Path
import sys
import unittest
import tempfile
from PIL import Image
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import ccor_hunter as ch
import compare_ccor_seed_search as comparison


def sequence(minutes=15, missing=(), moving=True):
    start = dt.datetime(2026, 10, 29, 20, tzinfo=dt.timezone.utc)
    frames = []
    for i in range(8):
        image = np.full((128,128), 40, dtype=np.float32)
        if i not in missing:
            x = 25 + (i if moving else 0)
            image[30, x] += 60
        frames.append(image)
    times = [start+dt.timedelta(minutes=i*minutes) for i in range(8)]
    return frames, times, [(128,128)]*8


def run(minutes=15, missing=(), moving=True, enhanced=False):
    return ch.track_points(*sequence(minutes,missing,moving), 'ccor1',
                           complete_search=True, return_diagnostics=True,
                           multi_baseline_seeds=enhanced)


class MultiBaselineTests(unittest.TestCase):
    def test_recovers_gap_cadence_rejected_by_adjacent_time_limit(self):
        before, _ = run(minutes=45)
        after, info = run(minutes=45, enhanced=True)
        self.assertEqual(before, [])
        self.assertTrue(after)
        self.assertTrue(info['scan_completed'])
        self.assertTrue(any(r['frames']==8 and r['precap_review_eligible'] for r in after))

    def test_recovers_earlier_observation_across_missing_peak(self):
        before, _ = run(missing=(1,))
        after, _ = run(missing=(1,), enhanced=True)
        self.assertTrue(before)
        self.assertTrue(after)
        self.assertGreater(max(r['frames'] for r in after), max(r['frames'] for r in before))
        self.assertTrue(any(r['frames']==7 and r['precap_review_eligible'] for r in after))

    def test_regular_source_still_recovered(self):
        for enhanced in (False,True):
            rows, _ = run(enhanced=enhanced)
            self.assertTrue(any(r['frames']==8 for r in rows))

    def test_static_control_stays_empty(self):
        for enhanced in (False,True):
            rows, _ = run(moving=False,enhanced=enhanced)
            self.assertEqual(rows, [])

    def test_seeds_are_bounded_and_points_unique(self):
        _, times, _ = sequence(minutes=45)
        pairs = list(ch.seed_frame_pairs(times,True))
        self.assertTrue(pairs)
        self.assertLessEqual(len(pairs),3*(len(times)-3))
        for a,b in pairs:
            self.assertLessEqual(b-a,3)
            self.assertLessEqual((times[b]-times[a]).total_seconds(),5400)
        rows, info = run(enhanced=True)
        self.assertLessEqual(len(rows),40)
        self.assertEqual(info['per_frame_peak_cap'],70)
        for row in rows:
            stamps = [p['time'] for p in row['points']]
            self.assertEqual(stamps,sorted(set(stamps)))

    def test_cached_comparison_preserves_input_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache=Path(tmp)
            frames,times,_=sequence(minutes=45)
            for frame,when in zip(frames,times):
                name=when.strftime('%Y%m%d_%H%M')+'_ccor1_1024by960.jpg'
                Image.fromarray(frame.astype(np.uint8)).save(cache/name,quality=100)
            before={p.name:p.read_bytes() for p in cache.iterdir()}
            result=comparison.compare('ccor1',cache)
            self.assertEqual(result['status'],'COMPLETE_COMPARISON')
            self.assertIsNone(result['reported_comet_recovery'])
            self.assertTrue(result['same_in_memory_frames'])
            self.assertEqual(before,{p.name:p.read_bytes() for p in cache.iterdir()})
            self.assertEqual(comparison.compare('ccor2',cache)['status'],'NO_CACHED_IMAGES')

    def test_too_sparse_source_does_not_bypass_pixel_continuity(self):
        rows,_ = run(missing=(1,3,5,7),enhanced=True)
        self.assertTrue(rows)
        self.assertFalse(any(r['precap_review_eligible'] for r in rows))


if __name__ == '__main__':
    unittest.main()
