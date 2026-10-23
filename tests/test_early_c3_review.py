"""Early C3 notifications fail closed and preserve physical trajectory IDs."""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import early_c3_review as early
import watch_early_c3_email as notify

UTC=dt.timezone.utc
def sample(now):
    when=now.strftime("%Y%m%d_%H%M")
    row={
        "candidate":"C0120007","status":"UNMATCHED",
        "review_class":"STRONG_REVIEW","frames":5,
        "first":when+"_c3_1024.jpg","last":when+"_c3_1024.jpg",
        "resolution":1024,"trajectory_warning":"",
        "edge_review":{"status":"CLEAR"},
        "star_field":{"status":"NO_CONSENSUS"},
        "pixel_verification":{
            "hits":5,"samples":5,"hit_fraction":1.0,
            "longest_hit_run":5,"peak_snr":12.,
            "temporal_motion":{"tested":2,"supported":2,"support_fraction":1.0}
        },
        "track":{"last_x":400,"last_y":500,"vx":-2,"vy":-3},
        "early_alert_reasons":[],"report_check_status":"CHECKED_RECENT_LIST"
    }
    row["track"]={"first_x":400,"first_y":500,"last_x":398,"last_y":497,
                  "vx":-2.0,"vy":-3.0}
    return row

class EarlyReviewTests(unittest.TestCase):
    def test_good_unmatched_track_eligible(self):
        now=dt.datetime(2026,10,23,14,0,tzinfo=UTC)
        self.assertEqual(early.eligibility(sample(now),reports_ok=True,now=now),[])
    def test_reports_unavailable_never_alerts(self):
        now=dt.datetime(2026,10,23,14,0,tzinfo=UTC)
        self.assertIn("REPORT_CHECK_UNAVAILABLE_OR_MATCHED",
                      early.eligibility(sample(now),reports_ok=False,now=now))
    def test_matched_reports_never_alert(self):
        now=dt.datetime(2026,10,23,14,0,tzinfo=UTC)
        row=sample(now);row["status"]="KNOWN_REPORT"
        self.assertTrue(early.eligibility(row,reports_ok=True,now=now))
    def test_temporal_evidence_and_freshness_required(self):
        now=dt.datetime(2026,10,23,14,0,tzinfo=UTC)
        row=sample(now)
        row["pixel_verification"]["temporal_motion"]["supported"]=0
        self.assertIn("INSUFFICIENT_INDEPENDENT_MOTION",
                      early.eligibility(row,reports_ok=True,now=now))
        row=sample(now)
        row["last"]="20261023_1000_c3_1024.jpg"
        self.assertIn("NOT_A_FRESH_TRACK",
                      early.eligibility(row,reports_ok=True,now=now))
    def test_candidate_id_not_physical_identity(self):
        now=dt.datetime(2026,10,23,14,0,tzinfo=UTC)
        a=sample(now)
        b=sample(now)
        b["track"]["last_x"]=650
        self.assertFalse(early.same_early_track(a,b))
        self.assertTrue(early.same_early_track(a,a))
    def test_512_1024_identity(self):
        now=dt.datetime(2026,10,23,14,0,tzinfo=UTC)
        a=sample(now)
        b=sample(now)
        b["resolution"]=512
        b["track"]={"last_x":200,"last_y":250,"vx":-1,"vy":-1.5}
        self.assertTrue(early.same_early_track(a,b))
    def test_notification_gating_and_dedup(self):
        now=dt.datetime.now(UTC)
        row=sample(now)
        row["status"]="EARLY_ALERT_READY"
        payload={"mode":"EARLY_REVIEW_UNCONFIRMED",
                 "report_check_status":"CHECKED_RECENT_LIST",
                 "report_count":15,
                 "notify_enabled":True,
                 "generated_at":now.isoformat(),"candidates":[row]}
        self.assertEqual(len(notify.due_for_dispatch(payload,[],now)),1)
        self.assertEqual(notify.due_for_dispatch(payload,[row],now),[])
        payload["report_check_status"]="UNAVAILABLE"
        self.assertEqual(notify.due_for_dispatch(payload,[],now),[])
        payload["report_check_status"]="CHECKED_RECENT_LIST"
        row["report_id"]="report_known"
        self.assertEqual(notify.due_for_dispatch(payload,[],now),[])

    def test_reporting_failure_prevents_eligibility(self):
        now=dt.datetime(2026,10,23,14,0,tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temp:
            args=SimpleNamespace(resolution=1024)
            # A parsed early track uses datetime objects and has no reporting match.
            c={
                "cid":"C0120007","resolution":1024,"priority":"MEDIUM",
                "first":{"time":now,"file":"20261023_1400_c3_1024.jpg","x":400,"y":500},
                "last":{"time":now+dt.timedelta(minutes=36),"file":"20261023_1436_c3_1024.jpg","x":397,"y":495},
                "frames":5,"vx":-5,"vy":-8,"rms":0.5,"score":20.0,"sunward":5.0,
                "speed":9.4,"cv":0.2,
            }
            class Stub:
                def audit_track_frames(self,c,f):pass
                def comet_like(self,c):return True
                def live_candidate_rank(self,c):return 1
                def verify_track_pixels(self,c,f,gray_cache=None):return{
                    "status":"STRONG","hits":5,"samples":5,"hit_fraction":1,
                    "peak_snr":12,"longest_hit_run":5,"evidence_score":80}
                def classify_for_review(self,c,p):return ("STRONG_REVIEW",False)
                def verify_temporal_motion(self,c,f,gray_cache=None):return{
                    "tested":2,"supported":2,"support_fraction":1.0}
                def edge_review_evidence(self,c):return {"status":"CLEAR"}
                def annotate_star_field(self,rows):pass
                def parse_reports(self,page):return []
            with patch.object(early.dt,"datetime",wraps=dt.datetime) as mocked:
                mocked.now.return_value=now+dt.timedelta(minutes=40)
                result=early.process_early_tracks(
                    [c],[Path("20261023_1436_c3_1024.jpg")],
                    Path(temp),args,"sample",Stub(),report_fetch=lambda:"bad")
            self.assertEqual(result["report_check_status"],"UNAVAILABLE")
            self.assertEqual(result["early_alert_ready"],0)
            self.assertTrue((Path(temp)/"early_candidates.json").exists())

if __name__=="__main__":
    unittest.main()
