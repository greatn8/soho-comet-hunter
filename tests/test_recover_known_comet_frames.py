"""SOHO archive input recovery tests without network requests."""
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from recover_known_comet_frames import day_folders, select_links
from benchmark_known_comets import when


class RecoveryTests(unittest.TestCase):
    def test_intervals_across_utc_midnight(self):
        a=when("2026-10-18T23:00:00Z")
        b=when("2026-10-19T01:00:00Z")
        self.assertEqual(list(day_folders(a,b)),
                         ["2026/c3/20261018", "2026/c3/20261019"])

    def test_only_correct_resolution_and_date_is_selected(self):
        html="""
          <a href="20260730_0230_c3_1024.jpg">1024</a>
          <a href="20260730_0242_c3_512.jpg">512</a>
          <a href="20260730_1000_c3_1024.jpg">too late</a>
          <a href="20260730_0230_c2_1024.jpg">wrong camera</a>
        """
        start=when("2026-07-30T02:00:00Z")
        finish=when("2026-07-30T06:00:00Z")
        self.assertEqual(
            select_links(html,start,finish,(1024,)),
            ["20260730_0230_c3_1024.jpg"])
        self.assertEqual(
            select_links(html,start,finish,(512,1024)),
            ["20260730_0230_c3_1024.jpg", "20260730_0242_c3_512.jpg"])


if __name__=="__main__":
    unittest.main()
