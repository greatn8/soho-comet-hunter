"""C3 identifiers must never recycle, including across worker restarts."""
import json
import pathlib
import sys
import tempfile
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"tools"))
from c3_candidate_ids import allocate_candidate_ids,COUNTER_NAME

class CandidateIdentityTests(unittest.TestCase):
    def test_restart_maintains_monotonic_unique_numbering(self):
        with tempfile.TemporaryDirectory() as temp:
            p=pathlib.Path(temp)
            one=allocate_candidate_ids(p,3)
            two=allocate_candidate_ids(p,2)
            self.assertEqual(one,["C9000000000001","C9000000000002","C9000000000003"])
            self.assertEqual(two,["C9000000000004","C9000000000005"])
            self.assertEqual(len(set(one+two)),5)
    def test_recovery_fails_safe_after_primary_state_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            p=pathlib.Path(temp)
            allocate_candidate_ids(p,4)
            (p/"state"/COUNTER_NAME).unlink()
            self.assertEqual(allocate_candidate_ids(p,1),["C9000000000005"])
    def test_corrupt_primary_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            p=pathlib.Path(temp)
            allocate_candidate_ids(p,1)
            (p/"state"/COUNTER_NAME).write_text("corrupted")
            with self.assertRaises((ValueError,json.JSONDecodeError)):
                allocate_candidate_ids(p,1)
    def test_orphaned_counter_refuses_to_restart_from_one(self):
        with tempfile.TemporaryDirectory() as temp:
            p=pathlib.Path(temp)
            d=p/"results"/"realtime";d.mkdir(parents=True)
            (d/"latest_candidates.json").write_text('[{"candidate":"C9000000000003"}]')
            with self.assertRaisesRegex(RuntimeError,"recover"):
                allocate_candidate_ids(p,1)
    def test_zero_does_not_reserve_numbers(self):
        with tempfile.TemporaryDirectory() as temp:
            p=pathlib.Path(temp)
            self.assertEqual(allocate_candidate_ids(p,0),[])
            self.assertEqual(allocate_candidate_ids(p,1),["C9000000000001"])

if __name__=="__main__":
    unittest.main()
