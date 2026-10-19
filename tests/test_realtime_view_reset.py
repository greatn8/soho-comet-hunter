"""Display-only realtime reset marker tests."""
import datetime as dt
import pathlib
import sys
import tempfile
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"tools"))
from update_dashboard import realtime_view_reset_time, realtime_row_visible

class TestRealtimeReset(unittest.TestCase):
    def test_filter_by_last_observation_not_processing_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root=pathlib.Path(temp)
            (root/"dashboard_view_reset_at.txt").write_text("2026-10-19T07:00:00Z\n")
            reset=realtime_view_reset_time(root)
            self.assertEqual(reset, dt.datetime(2026,10,19,7,tzinfo=dt.timezone.utc))
            self.assertFalse(realtime_row_visible({"first":"20261018_0700_c3_512.jpg","last":"20261019_0654_c3_512.jpg"}, reset))
            self.assertTrue(realtime_row_visible({"first":"20261019_0654_c3_512.jpg","last":"20261019_0706_c3_512.jpg"}, reset))
            self.assertFalse(realtime_row_visible({"last":"bad"},reset))
            self.assertFalse(realtime_row_visible({},reset))
            self.assertTrue(realtime_row_visible({"last":"bad"},None))
    def test_invalid_marker_does_not_hide_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root=pathlib.Path(temp)
            (root/"dashboard_view_reset_at.txt").write_text("not-a-time")
            self.assertIsNone(realtime_view_reset_time(root))

if __name__=="__main__":
    unittest.main()
