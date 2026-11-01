"""Delivery contracts: normalisation cannot bypass the temporal policy."""
import copy
import datetime as dt
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from c3_motion_triage import primary_review
import comet_github_alert as alerts


def row():
    now = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d_%H%M') + '_c3_1024.jpg'
    return {'candidate': 'C_TEST', 'review_id': 'RT_TEST', 'status': 'UNMATCHED',
            'review_class': 'STRONG_REVIEW', 'first': now, 'last': now, 'speed': 0.1,
            'pixel_verification': {'status': 'STRONG', 'hits': 5, 'hit_fraction': .6,
                'longest_hit_run': 4, 'temporal_motion': {'status': 'MEASURED',
                    'tested': 4, 'supported': 2, 'support_fraction': .5}}}


def cases():
    result = [row()]
    for key, value in [('status', 'UNAVAILABLE'), ('tested', 2), ('supported', 1),
                       ('support_fraction', .49), ('support_fraction', float('nan')),
                       ('supported', 7)]:
        c = row(); c['pixel_verification']['temporal_motion'][key] = value
        result.append(c)
    for field in ('star_field', 'image_star_motion'):
        c = row(); c[field] = {'status': 'STAR_LIKE'}; result.append(c)
    for field, value in [('trajectory_warning', 'bad fit'), ('quality_issues', ['BAD'])]:
        c = row(); c[field] = value; result.append(c)
    c = row(); c['pixel_verification'] = {}; result.append(c)
    for field, value in [('status', 'KNOWN_REPORT'), ('report_id', 'report_x'),
                         ('known_object', 'SOHO-test'), ('starred', True),
                         ('review_class', 'SECONDARY')]:
        c = copy.deepcopy(result[-1]); c[field] = value; result.append(c)
    return result


class DeliveryTests(unittest.TestCase):
    def test_notifications_require_measured_motion_after_normalisation(self):
        self.assertTrue(alerts.is_recent_strong(alerts.normalize(row())))
        for c in cases()[1:12]:
            with self.subTest(candidate=c):
                self.assertFalse(alerts.is_recent_strong(alerts.normalize(c)))

    def test_dispatch_and_push_cannot_emit_for_brightness_only(self):
        c = row(); c['pixel_verification']['temporal_motion'] = {'status': 'UNAVAILABLE'}
        for name, data in [('push', {'review_candidates': [c]}),
                           ('repository_dispatch', {'client_payload': {'candidate': c}})]:
            with patch.object(alerts, 'read_json', return_value=data), \
                 patch.object(alerts, 'existing_alerts', return_value=[]), \
                 patch.object(alerts, 'github_api') as api, \
                 patch('check_recent_sungrazer_reports.fetch_and_check') as check:
                alerts.run_issue(Path('unused'), name, 'test-token', 12)
                api.assert_not_called()
                check.assert_not_called()

    @unittest.skipUnless(shutil.which('node'), 'Node required for dashboard parity')
    def test_dashboard_and_python_policy_agree(self):
        # Extract actual production functions, not a copy of the JS policy.
        source = Path(__file__).resolve().parents[1] / 'docs/app.js'
        js = source.read_text()
        start = js.index('function c3MotionSupported(')
        end = js.index('\nfunction realtimeReviewGroups', start)
        script = "const normalizeClass = x => String(x || '').toUpperCase();\n" + js[start:end]
        script += "\nconst rows=JSON.parse(require('fs').readFileSync(0,'utf8')); process.stdout.write(JSON.stringify(rows.map(r=>!isRealtimeDiagnosticOnly(r))));"
        samples = cases()
        # JSON has no NaN; null is likewise ineligible in both runtimes.
        samples[5]['pixel_verification']['temporal_motion']['support_fraction'] = None
        result = subprocess.run(['node', '-e', script], input=json.dumps(samples),
                                text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), [primary_review(c) for c in samples])

    def test_old_alerts_and_direct_links_do_not_bypass_secondary(self):
        js = (Path(__file__).resolve().parents[1] / 'docs/app.js').read_text()
        self.assertIn('!isRealtimeDiagnosticOnly(row) && githubEmailForReview', js)
        self.assertIn('const latestAlert = bannerEligible ? rawLatestAlert : "";', js)
        self.assertIn('if (isRealtimeDiagnosticOnly(row)) state.showLowSupportRealtime = true;', js)


if __name__ == '__main__':
    unittest.main()
