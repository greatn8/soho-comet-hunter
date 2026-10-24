"""48h realtime retention keeps known objects, confirmed comets and historical records."""
import json,sys,tempfile,unittest
from pathlib import Path
import datetime as dt
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import prune_realtime_archive as p
UTC=dt.timezone.utc
NOW=dt.datetime(2026,10,24,6,tzinfo=UTC)
def row(id,cls="LOW_SUPPORT",days=3,**extra):
 return {"id":id,"event_id":id,"archived_from_realtime":True,
   "review_class":cls,"last_seen":(NOW-dt.timedelta(days=days)).isoformat(),**extra}
class Retention(unittest.TestCase):
 def test_protected(self):
  self.assertFalse(p.expired_unconfirmed(row("RT20261020T1200_A","KNOWN_REPORT"),NOW-dt.timedelta(hours=48)))
  self.assertFalse(p.expired_unconfirmed(row("RT20261020T1200_B","LOW_SUPPORT",known_object="C/2026 A1"),NOW-dt.timedelta(hours=48)))
  self.assertFalse(p.expired_unconfirmed(row("RT20261020T1200_C","CONFIRMED_COMET"),NOW-dt.timedelta(hours=48)))
  self.assertFalse(p.expired_unconfirmed(row("RT20261020T1200_D","LOW_SUPPORT",starred=True),NOW-dt.timedelta(hours=48)))
  self.assertFalse(p.expired_unconfirmed({"id":"x","last_seen":NOW.isoformat()},NOW-dt.timedelta(hours=48)))
  self.assertTrue(p.expired_unconfirmed(row("RT20261020T1200_E"),NOW-dt.timedelta(hours=48)))
 def test_dry_run_and_apply(self):
  with tempfile.TemporaryDirectory() as td:
   docs=Path(td)/"docs";(docs/"data").mkdir(parents=True)
   doomed=row("RT20261020T1200_ABC"); known=row("RT20261020T1200_DEF","KNOWN_REPORT")
   historical={"id":"EV001","review_class":"CONFIRMED_COMET"}
   (docs/"data/realtime_archive.json").write_text(json.dumps({"candidates":[doomed,known]}))
   (docs/"data/candidates.json").write_text(json.dumps({"candidates":[historical,doomed,known]}))
   for ident in ("RT20261020T1200ABC","RT20261020T1200DEF"):
    directory=docs/"media/realtime_archive"/ident;directory.mkdir(parents=True)
    (directory/"zoom_contact_sheet.jpg").write_bytes(b"fakejpg")
   p.execute(docs,apply=False,now=NOW)
   self.assertTrue((docs/"media/realtime_archive/RT20261020T1200ABC/zoom_contact_sheet.jpg").exists())
   p.execute(docs,apply=True,now=NOW)
   a=json.loads((docs/"data/realtime_archive.json").read_text())
   b=json.loads((docs/"data/candidates.json").read_text())
   self.assertEqual([x["id"] for x in a["candidates"]],[known["id"]])
   self.assertEqual([x["id"] for x in b["candidates"]],[historical["id"],known["id"]])
   self.assertFalse((docs/"media/realtime_archive/RT20261020T1200ABC/zoom_contact_sheet.jpg").exists())
   self.assertTrue((docs/"media/realtime_archive/RT20261020T1200DEF/zoom_contact_sheet.jpg").exists())
if __name__=="__main__":unittest.main()
