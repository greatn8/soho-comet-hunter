import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import replay_ccor_reference_cases as replay
from score_ccor_reference_cases import score


def fixture():
    times = ['2026-10-29T20:%02d:00Z' % m for m in (0, 15, 30)]
    reference = dict(reference_label='endorsed_potential_comet',
        points=[[t, 20+i, 30+i] for i, t in enumerate(times)])
    points = [dict(time=t, x=20+i, y=30+i) for i, t in enumerate(times)]
    track = dict(id='example', blind_rank=1, points=points,
        source_jpeg_shape=[512, 512], processing_shape=[512, 512],
        precap_review_eligible=True)
    blind = dict(status='BLIND_REPLAY_COMPLETE', processed_times=times,
        runs=[dict(strategy='MULTI_BASELINE', ranked_tracks=[track],
                   isolated_shortlist_ids=['example'])])
    landmarks = [dict(role=role, native_xy=p, jpeg_xy=p) for role, ps in (
        ('train', [[0, 0], [100, 0], [0, 100], [100, 100]]),
        ('validation', [[25, 30], [80, 60], [30, 80]])) for p in ps]
    calibration = dict(independent_of_report_positions=True,
                       provenance='Synthetic independent controls', landmarks=landmarks)
    return blind, reference, calibration


class RecoveryTests(unittest.TestCase):
    def test_missing_registration_is_unassessed_even_with_exact_track(self):
        b, r, _ = fixture()
        self.assertIsNone(score(b, r)['reported_object_recovered'])

    def test_matching_path_reaches_shortlist(self):
        b, r, c = fixture()
        result = score(b, r, c)
        self.assertTrue(result['runs'][0]['shortlisted_report_path'])

    def test_track_count_is_not_recovery(self):
        b, r, c = fixture()
        for point in b['runs'][0]['ranked_tracks'][0]['points']:
            point['x'] += 40
        self.assertFalse(score(b, r, c)['reported_object_recovered'])

    def test_observed_points_required_not_only_fitted_path(self):
        b, r, c = fixture()
        b['runs'][0]['ranked_tracks'][0]['points'].pop()
        self.assertFalse(score(b, r, c)['reported_object_recovered'])

    def test_bad_heldout_registration_is_unassessed(self):
        b, r, c = fixture()
        c['landmarks'][-1]['jpeg_xy'] = [200, 200]
        self.assertIsNone(score(b, r, c)['reported_object_recovered'])

    def test_source_gap_is_unassessed(self):
        b, r, c = fixture()
        b['processed_times'].pop()
        self.assertIsNone(score(b, r, c)['reported_object_recovered'])

    def test_replay_uses_production_flags_and_same_frame_objects(self):
        frames = [np.full((512, 512), 10, dtype=np.float32) for _ in range(6)]
        start = replay.parse_time('2026-10-29T20:00:00Z')
        import datetime as dt
        times = [start+dt.timedelta(minutes=15*i) for i in range(6)]
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(replay, 'acquire_frames', return_value=([], [])), \
                 patch.object(replay.ch, 'load_frames', return_value=(frames, times, [(512, 512)]*6)), \
                 patch.object(replay.ch, 'track_points', return_value=([], {'scan_completed':True})) as detect:
                row = replay.replay('ccor1-20261029-wage', Path(folder), True)
        self.assertEqual(row['status'], 'BLIND_REPLAY_COMPLETE')
        calls = detect.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIs(calls[0].args[0], calls[1].args[0])
        self.assertTrue(all(c.kwargs['complete_search'] for c in calls))
        self.assertFalse(calls[0].kwargs['multi_baseline_seeds'])
        self.assertTrue(calls[1].kwargs['multi_baseline_seeds'])


if __name__ == '__main__':
    unittest.main()
