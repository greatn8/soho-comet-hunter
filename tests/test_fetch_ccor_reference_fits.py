"""Offline reference FITS directory index and strict timestamp matching."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import fetch_ccor_reference_fits as f
from benchmark_ccor_reported_comets import CASES
class NativeDownloadTests(unittest.TestCase):
    def test_index_camera_and_path_restrictions(self):
        html='''<a href="CCOR2_1A_20261022T234500_V00_NC.fits">good</a>
        <a href="CCOR1_1A_20261022T234500_V00_NC.fits">bad camera</a>
        <a href="https://not-noaa.example/CCOR2_1A_20261022T234500_V00_NC.fits">external</a>'''
        rows=f.enumerate_index("ccor2",html)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["time"].strftime("%H:%M"),"23:45")
    def test_no_nearest_hour_substitution(self):
        case=CASES["ccor2-20261022-wage"]
        names=['CCOR2_1A_20261022T234500_V00_NC.fits',
               'CCOR2_1A_20261023T003000_V00_NC.fits',
               'CCOR2_1A_20261023T010000_V00_NC.fits',
               'CCOR2_1A_20261023T011500_V00_NC.fits']
        html="".join(f'<a href="{n}">{n}</a>' for n in names)
        rows=f.enumerate_index("ccor2",html)
        selected,missing=f.pick_samples(rows,case,context=0)
        self.assertEqual(len(selected),4)
        self.assertEqual(missing,[])
        selected,missing=f.pick_samples(rows,case,context=1)
        self.assertTrue(missing)
    def test_fits_magic(self):
        self.assertTrue(f.fits_header_signature_ok(b"SIMPLE  =                    T"))
        self.assertFalse(f.fits_header_signature_ok(b"<html>403 denied"))
if __name__=="__main__":unittest.main()
