"""Offline geometry and FITS observation tests, no FITS dependency needed."""
import datetime as dt
import pathlib
import sys
import unittest

import numpy as np
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"tools"))
import ccor_native_fits_audit as audit

class CCORNativeFitsAuditTests(unittest.TestCase):
    def test_date_and_midnight_routing(self):
        case="ccor2-20261022-wage"
        a=audit.report_for_case(case,[])
        self.assertEqual(a["report_observations"],4)
        self.assertEqual(a["matched_native_frames"],0)
        self.assertIsNone(a["confirmed_comet_recovery"])
        self.assertEqual(a["observation_results"][-1]["report_time_utc"],"2026-10-23T01:15:00Z")
    def test_ruan_followup_has_five_observations(self):
        result=audit.report_for_case("ccor1-20261022-ruan",[])
        self.assertEqual(result["report_observations"],5)
        self.assertEqual(result["observation_results"][-1]["report_time_utc"],"2026-10-22T13:30:00Z")
    def test_native_origin_flip(self):
        self.assertEqual(audit.native_pixel(1820,1718,(1920,2048),"upper_left"),(1718,1820))
        self.assertEqual(audit.native_pixel(1820,1718,(1920,2048),"lower_left"),(201,1820))
    def test_outside_bounds(self):
        self.assertIsNone(audit.native_pixel(3000,1718,(1920,2048),"upper_left"))
    def test_temporal_frame_selection(self):
        base=audit.parse_time("2026-10-23T00:30:00Z")
        samples=[{"timestamp":base+dt.timedelta(minutes=1),"camera":"ccor2"},
                 {"timestamp":base+dt.timedelta(minutes=9),"camera":"ccor2"}]
        self.assertEqual(audit.report_nearest(samples,base),samples[0])
        self.assertIsNone(audit.report_nearest(samples,base-dt.timedelta(minutes=6)))
    def test_flat_and_signal(self):
        a=np.ones((80,80),dtype=float)
        self.assertEqual(audit.local_signal(a,35,35,"upper_left")["status"],"FLAT_OR_MASKED")
        a[33:38,33:38]=12
        a[20:23,20:23]=0
        b=audit.local_signal(a,35,35,"upper_left")
        self.assertIn(b["status"],("FLAT_OR_MASKED","SAMPLED_NOT_VALIDATED"))
        self.assertEqual(b["row"],35)
    def test_nan_occulted_data(self):
        a=np.full((80,80),np.nan)
        self.assertEqual(audit.local_signal(a,35,35,"lower_left")["status"],
                         "MASKED_OR_INSUFFICIENT_DATA")
if __name__=="__main__":
    unittest.main()
