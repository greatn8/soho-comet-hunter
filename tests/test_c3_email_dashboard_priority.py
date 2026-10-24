"""GitHub Actions C3 email issues are indexed by stable review identity."""
import pathlib
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from update_dashboard import normalize_github_email_issues, load_github_email_issues


def issue(number, cid="C0120361", review_id="RT20261023T2254_51D644E0"):
    import json
    metadata = {"review_id": review_id, "candidate": cid,
                "first": "20261023_2254_c3_1024.jpg",
                "last": "20261024_0406_c3_1024.jpg"}
    return {"number": number, "created_at": "2026-10-24T09:26:18Z",
            "body": "<!-- SOHO_STRONG_REVIEW_ALERT " +
                    json.dumps(metadata, separators=(",", ":")) + " -->\n@greatn8"}


class C3EmailOrderTests(unittest.TestCase):
    def test_exact_issue_from_recent_alert(self):
        rows = normalize_github_email_issues([issue(890)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["candidate"], "C0120361")
        self.assertEqual(rows[0]["review_id"], "RT20261023T2254_51D644E0")
        self.assertEqual(rows[0]["issue_number"], 890)

    def test_unmarked_and_pull_request_ignored(self):
        self.assertEqual(normalize_github_email_issues([
            {"number":1, "created_at":"2026-10-24T09:26:18Z", "body":"Not an alert"},
            {**issue(2), "pull_request":{"url":"x"}}
        ]), [])

    def test_same_candidate_label_different_review_stays_distinct(self):
        rows = normalize_github_email_issues([issue(890), issue(891, review_id="RT20261023T2254_ABC123")])
        self.assertEqual(len(rows), 2)

    def test_failure_preserves_last_known_index(self):
        old = normalize_github_email_issues([issue(890)])
        result = load_github_email_issues({"github_email_alert_issues": old},
            fetch=Mock(side_effect=OSError("offline")))
        self.assertEqual(result, old)

    def test_issue_reader_fetches_bounded_pages(self):
        rows = load_github_email_issues({}, fetch=lambda _: [issue(890)])
        self.assertEqual(rows[0]["issue_number"], 890)


if __name__ == "__main__":
    unittest.main()
