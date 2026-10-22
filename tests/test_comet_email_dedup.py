"""Regression checks: only genuinely unmatched events may generate emails."""
import datetime as dt
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import comet_github_alert as alerts


def candidate(status):
    now=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M")
    return {"candidate":"C0120031","review_id":"RT_SAMPLE",
            "review_class":"STRONG_REVIEW","status":status,
            "last":f"{now}_c3_1024.jpg","first":f"{now}_c3_1024.jpg"}


class EmailDedupTests(unittest.TestCase):
    def test_only_unmatched_notifies(self):
        self.assertTrue(alerts.is_recent_strong(candidate("UNMATCHED")))
        for status in ("SEEN_ALREADY","KNOWN_REPORT","","UNKNOWN","SECONDARY"):
            with self.subTest(status=status):
                self.assertFalse(alerts.is_recent_strong(candidate(status)))

    def test_no_repeat_comment_when_issue_enriched(self):
        entry=candidate("UNMATCHED")
        entry["track"]={"first_x":100,"first_y":110,"last_x":120,
                        "last_y":130,"vx":3,"vy":2}
        prior={**entry,"position_row_count":0,"_issue_number":42}
        calls=[]
        def fake_api(method,endpoint,token,data=None):
            calls.append((method,endpoint,data))
            return {}
        with patch.object(alerts,"github_api",side_effect=fake_api), \
             patch.object(alerts,"existing_alerts",return_value=[prior]), \
             patch.object(alerts,"read_json",return_value={
                 "review_candidates":[entry]}), \
             patch("check_recent_sungrazer_reports.fetch_and_check",
                   return_value={"status":"CHECK_UNAVAILABLE"}):
            alerts.run_issue(Path("unused"),"push","token",12)
        self.assertEqual([method for method,endpoint,_ in calls],["PATCH"])
        self.assertIn("/issues/42",calls[0][1])
        self.assertEqual(calls[0][2].keys(),{"body"})

    def test_push_fallback_skips_seen_and_unknown(self):
        rows=[candidate(s) for s in ("SEEN_ALREADY","KNOWN_REPORT","UNKNOWN")]
        with patch.object(alerts,"read_json",return_value={
            "review_candidates":rows}), \
             patch.object(alerts,"github_api") as api, \
             patch.object(alerts,"existing_alerts",return_value=[]), \
             patch("check_recent_sungrazer_reports.fetch_and_check") as check:
            alerts.run_issue(Path("unused"),"push","token",12)
        api.assert_not_called()
        check.assert_not_called()


if __name__=="__main__":
    unittest.main()
