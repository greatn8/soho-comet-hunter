import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from c3_motion_triage import triage
from audit_c3_motion_triage import compare


def supported():
    return {'candidate': 'test', 'review_id': 'RT1', 'speed': .1,
            'pixel_verification': {'status': 'STRONG', 'hits': 8, 'hit_fraction': .8,
                'longest_hit_run': 8, 'temporal_motion': {'status': 'MEASURED',
                    'tested': 6, 'supported': 4, 'support_fraction': 4/6}}}


class TriageTests(unittest.TestCase):
    def test_slow_motion_is_not_rejected_by_speed(self):
        self.assertEqual(triage(supported())['stage'], 'MOTION_SUPPORTED_REVIEW')

    def test_brightness_alone_does_not_pass(self):
        row = supported()
        row['pixel_verification']['temporal_motion'] = {'status': 'UNAVAILABLE'}
        self.assertEqual(triage(row)['stage'], 'SECONDARY_REVIEW')

    def test_star_consensus_is_secondary_even_with_temporal_support(self):
        row = supported()
        row['image_star_motion'] = {'status': 'STAR_LIKE'}
        self.assertEqual(triage(row)['stage'], 'STAR_LIKE_REVIEW')

    def test_inconclusive_star_check_is_not_proof_of_nonstellar_motion(self):
        row = supported()
        row['image_star_motion'] = {'status': 'NO_CONSENSUS'}
        self.assertIn('still unverified', triage(row)['reason'])

    def test_known_bypass_cannot_manufacture_recall(self):
        row = {'review_id': 'old', 'known_object': 'SOHO-5143'}
        report = compare([row] * 20, [])
        control = report['historical_controls'][0]
        self.assertEqual(control['unique_reviews'], 1)
        self.assertEqual(control['result'], 'UNASSESSED')
        self.assertFalse(report['activation_allowed'])

    def test_sparse_known_label_survives_richer_unlabelled_record(self):
        sparse = {'review_id': 'RT1', 'known_object': 'known'}
        for rows in ([sparse, supported()], [supported(), sparse]):
            control = compare(rows, [])['historical_controls'][0]
            self.assertEqual(control['object_or_report'], 'known')
            self.assertEqual(control['assessed_reviews'], 1)

    def test_rich_duplicate_is_used_and_failed_known_control_is_visible(self):
        poor = {'review_id': 'RT1', 'known_object': 'known'}
        rich = supported()
        rich['known_object'] = 'known'
        rich['pixel_verification']['temporal_motion']['supported'] = 0
        rich['pixel_verification']['temporal_motion']['support_fraction'] = 0
        control = compare([rich, poor], [])['historical_controls'][0]
        self.assertEqual(control['assessed_reviews'], 1)
        self.assertEqual(control['result'], 'WOULD_DOWNGRADE')


if __name__ == '__main__':
    unittest.main()
