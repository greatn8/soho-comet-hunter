"""No transient unconfirmed clips in permanent main Git history."""
import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import update_dashboard as dash
import guard_archive_git_media as guard

class MediaPolicyTests(unittest.TestCase):
 def test_unconfirmed_promotion_does_not_copy_media(self):
  with tempfile.TemporaryDirectory() as tmp:
   base=Path(tmp);source=base/"source";data=base/"docs"/"data"
   data.mkdir(parents=True);(source/"review"/"RTDEMO").mkdir(parents=True)
   (source/"review"/"RTDEMO"/"zoom_annotated.mp4").write_bytes(b"x"*2048)
   c={"id":"RT20261022T1200_C0120111","review_class":"STRONG_REVIEW",
      "known_object":None}
   dash.preserve_realtime_archive_media(source,data,c,"RTDEMO")
   self.assertIsNone(c.get("video"))
   self.assertFalse((base/"docs/media/realtime_archive").exists())
 def test_known_report_remains_archive_eligible(self):
  with tempfile.TemporaryDirectory() as tmp:
   base=Path(tmp);source=base/"source";data=base/"docs"/"data"
   data.mkdir(parents=True);(source/"review"/"RTDEMO").mkdir(parents=True)
   (source/"review"/"RTDEMO"/"zoom_annotated.mp4").write_bytes(b"x"*2048)
   c={"id":"RT20261022T1200_C0120111","review_class":"KNOWN_REPORT",
      "known_object":"report_someone"}
   dash.preserve_realtime_archive_media(source,data,c,"RTDEMO")
   self.assertIn("realtime_archive",c["video"])
   self.assertTrue((base/"docs/media/realtime_archive"/"RT20261022T1200C0120111"/"zoom_annotated.mp4").exists())
 def test_staging_guard_rejects_unconfirmed(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/"docs"/"data").mkdir(parents=True)
   unknown={"id":"RT20261022T1200_C0120111","archived_from_realtime":True,
            "review_class":"STRONG_REVIEW"}
   (root/"docs"/"data"/"realtime_archive.json").write_text(json.dumps({"candidates":[unknown]}))
   (root/"docs"/"data"/"candidates.json").write_text(json.dumps({"candidates":[unknown]}))
   added=b"docs/media/realtime_archive/RT20261022T1200C0120111/zoom_annotated.mp4\0"
   with patch.object(guard.subprocess,"check_output",return_value=added):
    with self.assertRaises(ValueError):
     guard.verify(root)
if __name__=="__main__":unittest.main()
