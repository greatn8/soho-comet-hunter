"""Regression tests for early C3 GitHub issue-email delivery."""
import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import comet_github_alert as alerts
import watch_early_c3_email as email

def candidate():
    now=dt.datetime.now(dt.timezone.utc)
    stamp=now.strftime("%Y%m%d_%H%M")
    return {
      "candidate":"C0120111","resolution":1024,
      "first":stamp+"_c3_1024.jpg","last":stamp+"_c3_1024.jpg",
      "status":"EARLY_ALERT_READY","report_check_status":"CHECKED_RECENT_LIST",
      "report_id":"","early_alert_reasons":[],"review_class":"STRONG_REVIEW",
      "frames":6,"pixel_verification":{"hits":6,"samples":6,"hit_fraction":1,
      "longest_hit_run":6,"peak_snr":12,"temporal_motion":{
      "tested":2,"supported":2,"support_fraction":1}},
      "trajectory_warning":"","star_field":{"status":"NO_CONSENSUS"},
      "edge_review":{"status":"CLEAR"},
      "track":{"first_x":300,"first_y":350,"last_x":299,"last_y":345,"vx":-1,"vy":-4}
    }
def payload(row=None):
    return {"mode":"EARLY_REVIEW_UNCONFIRMED",
      "report_check_status":"CHECKED_RECENT_LIST","report_count":16,
      "notify_enabled":True,"generated_at":dt.datetime.now(dt.timezone.utc).isoformat(),
      "candidates":[row or candidate()]}
class EarlyEmail(unittest.TestCase):
    def test_payload_gates(self):
        now=dt.datetime.now(dt.timezone.utc)
        self.assertEqual(len(email.recent_eligible(payload(),now)),1)
        for field,val in [("report_check_status","UNAVAILABLE"),("notify_enabled",False),("report_count",0)]:
            p=payload();p[field]=val
            self.assertEqual(email.recent_eligible(p,now),[])
    def test_known_and_bad_pixel_gates(self):
        now=dt.datetime.now(dt.timezone.utc)
        for key,val in [("status","KNOWN_REPORT"),("report_id","report_1"),("early_alert_reasons",["DUPLICATE"])]:
            row=candidate();row[key]=val
            self.assertEqual(email.recent_eligible(payload(row),now),[])
    def test_same_id_different_object_not_deduped(self):
        now=dt.datetime.now(dt.timezone.utc)
        row=candidate();other=candidate();other["track"]["last_x"]=600
        self.assertEqual(len(email.due_for_dispatch(payload(row),[other],now)),1)
        self.assertEqual(email.due_for_dispatch(payload(row),[row],now),[])
    def test_early_event_uses_separate_gate(self):
        c=candidate()
        self.assertTrue(alerts.early_eligible_normalized(c))
        self.assertFalse(alerts.is_recent_strong(alerts.normalize(c)))
        c["status"]="SEEN_ALREADY"
        self.assertFalse(alerts.early_eligible_normalized(c))
    def test_github_receiver_fails_closed_on_report_outage(self):
        c=candidate()
        event={"action":"soho-early-review","client_payload":{"candidate":c}}
        with patch.object(alerts,"read_json",return_value=event), \
             patch.object(alerts,"github_api") as api, \
             patch.object(alerts,"existing_alerts",return_value=[]), \
             patch("check_recent_sungrazer_reports.fetch_and_check",
                   return_value={"status":"CHECK_UNAVAILABLE","reports_parsed":0}):
            alerts.run_issue(Path("unused"),"repository_dispatch","token",12)
        api.assert_not_called()
    def test_github_receiver_emits_early_issue_after_fresh_report_check(self):
        c=candidate()
        event={"action":"soho-early-review","client_payload":{"candidate":c}}
        calls=[]
        def fake_api(method,endpoint,token,data=None):
            calls.append((method,endpoint,data))
            return {"number":202}
        with patch.object(alerts,"read_json",return_value=event), \
             patch.object(alerts,"github_api",side_effect=fake_api), \
             patch.object(alerts,"existing_alerts",return_value=[]), \
             patch("check_recent_sungrazer_reports.fetch_and_check",
                   return_value={"status":"NO_CLOSE_POSITIONAL_MATCH","reports_parsed":10}):
            alerts.run_issue(Path("unused"),"repository_dispatch","token",12)
        self.assertEqual(len(calls),1)
        self.assertIn("EARLY REVIEW",calls[0][2]["title"])
        self.assertIn("UNCONFIRMED",calls[0][2]["body"])
if __name__=="__main__":
    unittest.main()
