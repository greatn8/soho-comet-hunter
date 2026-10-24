"""Offline regression tests for the isolated CCOR review hunter."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import ccor_hunter as ch

class CCORTests(unittest.TestCase):
    def test_index_discovery_and_camera_isolation(self):
        now=dt.datetime(2026,10,23,12,tzinfo=dt.timezone.utc)
        data=b'<a href="20261023_1100_ccor1_1024by960.jpg">one</a> <a href="20261023_1100_ccor2_1024by960.jpg">two</a>'
        found,errors=ch.discover("ccor1",now,fetch=lambda url:data)
        self.assertEqual([x["name"] for x in found],["20261023_1100_ccor1_1024by960.jpg"])
        self.assertFalse(errors)
    def test_no_cross_camera_coordinate_matching(self):
        a={"camera":"ccor1","first":"2026-10-23T10:00:00Z","x_first":140,"y_first":120,"vx_px_h":1,"vy_px_h":1}
        b={**a,"camera":"ccor2"}
        self.assertFalse(ch.match_track(a,b,None,None))
        self.assertTrue(ch.match_track(a,a,None,None))
    def test_distinct_id_integration(self):
        now=dt.datetime(2026,10,23,12,tzinfo=dt.timezone.utc)
        a={"id":"CCOR1-123","camera":"ccor1","first":"2026-10-23T10:00:00Z","last":"2026-10-23T11:00:00Z","frames":5,"x_first":140,"y_first":120,"vx_px_h":1,"vy_px_h":1}
        b={**a,"id":"CCOR1-456","frames":7}
        found=ch.integrate_events([a],[b],"ccor1",now)
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]["id"],"CCOR1-123")
        self.assertEqual(found[0]["frames"],7)
    def test_duplicate_prior_ids_are_consolidated_without_losing_best_fit(self):
        now=dt.datetime(2026,10,23,12,tzinfo=dt.timezone.utc)
        base={"id":"CCOR1-same","camera":"ccor1","first":"2026-10-23T10:00:00Z",
              "last":"2026-10-23T11:00:00Z","frames":4,"rms_px":1.2,
              "x_first":140,"y_first":120,"vx_px_h":1,"vy_px_h":1}
        better={**base,"frames":8,"rms_px":0.3}
        got=ch.integrate_events([base,better],[],"ccor1",now)
        self.assertEqual(len(got),1)
        self.assertEqual(got[0]["frames"],8)

    def test_review_shortlist_keeps_spatially_distinct_track(self):
        common={"camera":"ccor1","first":"2026-10-23T10:00:00Z",
                "last":"2026-10-23T11:00:00Z","frames":5,"rms_px":0.8,
                "processing_shape":[512,512]}
        crowded=[{**common,"id":f"crowded-{i}","x_first":150,"y_first":420}
                 for i in range(10)]
        distinct={**common,"id":"distinct","x_first":430,"y_first":120}
        picked=ch.review_shortlist(crowded+[distinct],limit_per_camera=5)
        self.assertEqual(len(picked),5)
        self.assertIn("distinct",picked)
        self.assertEqual(len(set(picked)),5)

    def test_manifest_uses_only_existing_cached_filenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            now = dt.datetime(2026, 10, 24, 6, tzinfo=dt.timezone.utc)
            (cache / "20261024_0500_ccor1_1204by1205.jpg").write_bytes(b"test")
            (cache / "20261024_0515_ccor2_1204by1205.jpg").write_bytes(b"test")
            (cache / "20261023_0100_ccor1_1204by1205.jpg").write_bytes(b"test")
            manifest = ch.source_frame_manifest(cache, "ccor1", now)
            self.assertEqual(len(manifest), 1)
            self.assertEqual(manifest[0]["time"], "2026-10-24T05:00:00Z")
            self.assertEqual(
                manifest[0]["image_url"],
                "https://services.swpc.noaa.gov/products/ccor1/jpegs/20261024_0500_ccor1_1204by1205.jpg"
            )

    def test_offline_manifest_is_separate_and_sends_no_alerts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            now = dt.datetime(2026, 10, 24, 6, tzinfo=dt.timezone.utc)
            cache = project / "cache" / "ccor" / "ccor1"
            cache.mkdir(parents=True)
            (cache / "20261024_0500_ccor1_1204by1205.jpg").write_bytes(b"test")
            payload = ch.run_once(project, now=now, offline=True)
            self.assertEqual(len(payload["frame_manifests"]["ccor1"]), 1)
            self.assertFalse(payload["automatic_email_alerts"])
            self.assertFalse((project / "results" / "realtime").exists())

    def test_ccor1_support_arm_mask_rejects_known_edge_points(self):
        for x, y in ((180, 375), (192, 349), (168, 393), (175, 410)):
            self.assertTrue(bool(ch.support_arm_mask("ccor1", (512,512))[y,x]))
        for x, y in ((60, 80), (450, 160), (390, 390)):
            self.assertFalse(bool(ch.support_arm_mask("ccor1", (512,512))[y,x]))
        self.assertFalse(ch.support_arm_mask("ccor2",(512,512)).any())

    def test_ccor1_arm_track_removed_from_retained_history(self):
        now=dt.datetime(2026,10,24,12,tzinfo=dt.timezone.utc)
        common={"camera":"ccor1","first":"2026-10-24T09:00:00Z",
                "last":"2026-10-24T10:00:00Z","frames":5,
                "processing_shape":[512,512],"vx_px_h":1,"vy_px_h":0}
        arm={**common,"id":"arm","x_first":192,"y_first":349,
             "x_last":190,"y_last":353,
             "points":[{"x":192,"y":349},{"x":190,"y":353}]}
        clear={**common,"id":"clear","x_first":420,"y_first":100,
               "x_last":425,"y_last":100,
               "points":[{"x":420,"y":100},{"x":425,"y":100}]}
        rows=ch.integrate_events([arm,clear],[arm],"ccor1",now)
        self.assertEqual([r["id"] for r in rows],["clear"])
        self.assertTrue(ch.track_hits_support_arm(arm))
        self.assertFalse(ch.track_hits_support_arm(clear))
        self.assertFalse(ch.track_hits_support_arm({**arm,"camera":"ccor2"}))

    def test_ccor2_support_arm_tracks_excluded_and_other_regions_retained(self):
        mask=ch.support_arm_mask("ccor2",(512,512))
        for x,y in ((253,153),(246,173),(234,149),(250,72)):
            self.assertTrue(bool(mask[y,x]),(x,y))
        for x,y in ((115,300),(360,305),(420,100)):
            self.assertFalse(bool(mask[y,x]),(x,y))
        base={"camera":"ccor2","processing_shape":[512,512],
              "first":"2026-10-24T09:00:00Z","last":"2026-10-24T10:00:00Z",
              "frames":5,"vx_px_h":1,"vy_px_h":0}
        bad={**base,"id":"arm","x_first":246,"y_first":173,
             "x_last":233,"y_last":148,
             "points":[{"x":246,"y":173},{"x":233,"y":148}]}
        good={**base,"id":"clear","x_first":360,"y_first":305,
              "x_last":363,"y_last":307,
              "points":[{"x":360,"y":305},{"x":363,"y":307}]}
        self.assertTrue(ch.track_hits_support_arm(bad))
        self.assertFalse(ch.track_hits_support_arm(good))
        now=dt.datetime(2026,10,24,12,tzinfo=dt.timezone.utc)
        self.assertEqual([r["id"] for r in ch.integrate_events([bad,good],[bad],"ccor2",now)],["clear"])
        self.assertFalse(ch.track_hits_support_arm({**bad,"camera":"ccor1"}))

    def test_timestamp_overlay_excludes_both_cameras(self):
        for camera in ("ccor1","ccor2"):
            mask=ch.timestamp_overlay_mask(camera,(512,512))
            self.assertTrue(bool(mask[489,130]))
            self.assertTrue(bool(mask[472,180]))
            self.assertFalse(bool(mask[489,220]))
            self.assertFalse(bool(mask[440,130]))
            row={"id":"timestamp","camera":camera,"processing_shape":[512,512],
                 "first":"2026-10-24T09:00:00Z","last":"2026-10-24T10:00:00Z",
                 "frames":5,"vx_px_h":1,"vy_px_h":0,
                 "x_first":135,"y_first":489,"x_last":107,"y_last":486,
                 "points":[{"x":135,"y":489},{"x":107,"y":486}]}
            self.assertTrue(ch.track_hits_timestamp_overlay(row))
            now=dt.datetime(2026,10,24,12,tzinfo=dt.timezone.utc)
            self.assertEqual(ch.integrate_events([row],[],camera,now),[])

    def test_timestamp_overlay_preserves_clear_points(self):
        row={"camera":"ccor2","processing_shape":[512,512],
             "points":[{"x":300,"y":480},{"x":301,"y":482}]}
        self.assertFalse(ch.track_hits_timestamp_overlay(row))
        row2={"camera":"ccor1","processing_shape":[1024,1024],
              "points":[{"x":270,"y":978},{"x":200,"y":970}]}
        self.assertTrue(ch.track_hits_timestamp_overlay(row2))

    def test_offline_writes_no_alerts(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload=ch.run_once(Path(tmp),now=dt.datetime(2026,10,23,12,tzinfo=dt.timezone.utc),offline=True)
            self.assertEqual(payload["mode"],"EXPERIMENTAL_REVIEW_ONLY")
            self.assertFalse(payload["automatic_email_alerts"])
            self.assertTrue((Path(tmp)/"results/ccor/ccor.json").exists())
            self.assertFalse((Path(tmp)/"results/realtime").exists())

if __name__=="__main__":
    unittest.main()
