import base64
import datetime as dt
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
from PIL import Image
try:
    from astropy.io import fits
except ImportError:
    fits = None
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import ccor_native_reporting as report


def sources():
    yy, xx = np.ogrid[:1920, :2048]
    data = (1+(xx+yy)/1000).astype(np.float32)
    rows = []
    for i in range(3):
        array = data.copy()
        array[300+i, 1200+i*3] = 100
        rows.append(dict(camera='ccor1', source_file=f'CCOR1_1B_20261022T12{i*15:02d}25_V00_NC.fits',
            time_utc=f'2026-10-22T12:{i*15:02d}:25.736000Z', source_sha256=str(i)*64,
            source_url=None, data=array))
    return rows


@unittest.skipUnless(fits is not None, 'Astropy tests run in the dedicated native-coordinate workflow')
class NativeReportingTests(unittest.TestCase):
    def test_exact_native_geometry_and_vertical_origin(self):
        self.assertEqual(report.native_pixel(0, 0), (1919, 0, [1, 1920]))
        self.assertEqual(report.native_pixel(2047, 1919), (0, 2047, [2048, 1]))
        for bad in [(-1, 0), (2048, 0), (1, 1920), (1.5, 20), (True, 1)]:
            with self.assertRaises(ValueError):
                report.native_pixel(*bad)

    def test_embedded_image_has_exact_source_pixel_at_reported_position(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'review.html'
            config = report.prepare(sources(), path)
            raw = base64.b64decode(config['frames'][0]['original'].split(',', 1)[1])
            image = Image.open(io.BytesIO(raw))
            self.assertEqual(image.size, (2048, 1920))
            self.assertEqual(image.getpixel((1200, 1619)), 255)
            self.assertLess(image.getpixel((1200, 300)), 255)
            self.assertIn('25.736000Z', config['frames'][0]['time_utc'])
            with self.assertRaises(FileExistsError):
                report.prepare(sources(), path)

    def test_local_fits_integrity_time_and_camera(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'CCOR1_1B_20261022T123025_V00_NC.fits'
            h = fits.ImageHDU(np.zeros(report.SHAPE, dtype=np.float32))
            h.header['DATE-OBS'] = '2026-10-22T12:30:25.736'
            fits.HDUList([fits.PrimaryHDU(), h]).writeto(path, checksum=True)
            loaded = report.load_source(path, 'ccor1')
            self.assertEqual(loaded['data'].shape, report.SHAPE)
            self.assertEqual(loaded['time_utc'], '2026-10-22T12:30:25.736000Z')
            with self.assertRaises(ValueError):
                report.load_source(path, 'ccor2')
            h.header['DATE-OBS'] = '2026-10-22T14:30:25.736'
            fits.HDUList([fits.PrimaryHDU(), h]).writeto(path, overwrite=True)
            with self.assertRaises(ValueError):
                report.load_source(path)

    def test_bounded_measured_time_selection(self):
        points = [dict(time='2026-10-22T%02d:%02d:00Z' % (12+i//4, (i%4)*15)) for i in range(18)]
        row = dict(id='test', camera='ccor2', points=points)
        camera, times = report.candidate_times(dict(tracks=[row]), 'test')
        self.assertEqual(camera, 'ccor2')
        self.assertEqual(len(times), 6)
        self.assertEqual(times[0], report.timestamp(points[0]['time']))
        self.assertEqual(times[-1], report.timestamp(points[-1]['time']))
        with self.assertRaises(ValueError):
            report.candidate_times(dict(tracks=[row, row]), 'test')

    def test_discovered_fits_must_match_camera_host_and_time(self):
        times = [report.timestamp('2026-10-22T12:%02d:00Z' % i) for i in (0, 15, 30)]
        names = [f'CCOR2_1A_20261022T12{i:02d}25_V00_NC.fits' for i in (0, 15, 30)]
        html = ''.join('<a href="'+n+'">FITS</a>' for n in names)
        html += '<a href="https://evil.invalid/'+names[0]+'">bad</a>'
        self.assertEqual([r[2] for r in report.select_sources('ccor2', times, html.encode())], names)
        with self.assertRaises(ValueError):
            report.select_sources('ccor1', times, html.encode())
        with self.assertRaises(ValueError):
            report.select_sources('ccor2', times+[report.timestamp('2026-10-22T13:00:00Z')], html.encode())

    def test_mixed_camera_and_repeated_observations_are_rejected(self):
        for field, value in [('camera', 'ccor2'), ('time_utc', sources()[0]['time_utc'])]:
            rows = sources()
            rows[-1][field] = value
            with tempfile.TemporaryDirectory() as d, self.assertRaises(ValueError):
                report.prepare(rows, Path(d)/'review.html')


if __name__ == '__main__':
    unittest.main()
