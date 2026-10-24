"""Keep CCOR rejected hypotheses locally; publish shortlisted tracks only."""
import copy
import pathlib
import sys
import tempfile
import unittest
import json
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"tools"))
from publish_ccor_shortlist import shortlisted_payload,publish

class CCORShortlistPublicationTests(unittest.TestCase):
    def sample(self):
        return {"mode":"EXPERIMENTAL_REVIEW_ONLY",
                "tracks":[{"id":"A","camera":"ccor1","points":[{"x":1}]},
                          {"id":"B","camera":"ccor1","points":[{"x":2}]},
                          {"id":"C","camera":"ccor2","points":[{"x":3}]}],
                "review_shortlist_ids":["C","A"],
                "temporal_overlaps_not_duplicates":[["A","B"],["A","C"]]}
    def test_publish_only_shortlist_in_order(self):
        source=self.sample()
        out=shortlisted_payload(source)
        self.assertEqual([t["id"] for t in out["tracks"]],["C","A"])
        self.assertEqual(out["local_retained_track_count"],3)
        self.assertEqual(out["published_per_camera"],{"ccor1":1,"ccor2":1})
        self.assertEqual(out["temporal_overlaps_not_duplicates"],[["A","C"]])
        self.assertEqual(len(source["tracks"]),3)
    def test_empty_shortlist_stays_empty_no_fallback(self):
        source=self.sample()
        source["review_shortlist_ids"]=[]
        self.assertEqual(shortlisted_payload(source)["tracks"],[])
    def test_invalid_missing_or_duplicate_identity_fails_closed(self):
        for ids in (None,["missing"],["A","A"]):
            source=self.sample()
            source["review_shortlist_ids"]=ids
            with self.assertRaises(ValueError):
                shortlisted_payload(source)
    def test_files_leave_original_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            src=pathlib.Path(directory)/"local.json"
            dst=pathlib.Path(directory)/"public.json"
            original=self.sample()
            src.write_text(json.dumps(original))
            publish(src,dst)
            self.assertEqual(len(json.loads(src.read_text())["tracks"]),3)
            self.assertEqual(len(json.loads(dst.read_text())["tracks"]),2)

if __name__=="__main__":
    unittest.main()
