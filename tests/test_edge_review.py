"""Parity-based border review tests, never permanent rejection."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from edge_review import edge_review_evidence


def c(size,x0,y0,x1,y1):
    return {"resolution":size,"first":{"x":x0,"y":y0},
            "last":{"x":x1,"y":y1}}


class BorderTests(unittest.TestCase):
    def test_equivalent_edges(self):
        for size in (512,1024):
            scale=size/512
            r=edge_review_evidence(c(size,100*scale,500*scale-1,
                                      110*scale,505*scale-1))
            self.assertEqual(r["status"],"BORDER_CONFINED",r)
            self.assertEqual(r["edge"],"BOTTOM")
    def test_inward_moving_object_kept(self):
        self.assertEqual(edge_review_evidence(
            c(1024,200,1005,222,910))["status"],"CLEAR")
    def test_interior_candidate_kept(self):
        self.assertEqual(edge_review_evidence(
            c(1024,200,840,220,910))["status"],"CLEAR")
    def test_left_edge(self):
        self.assertEqual(edge_review_evidence(
            c(1024,28,500,32,550))["edge"],"LEFT")
    def test_malformed_fail_open(self):
        self.assertEqual(edge_review_evidence(
            c(1024,-3,500,-1,550))["status"],"UNAVAILABLE")
    def test_512_and_1024_threshold(self):
        self.assertEqual(edge_review_evidence(
            c(512,200,483,210,483))["status"],"CLEAR")
        self.assertEqual(edge_review_evidence(
            c(1024,400,966,420,966))["status"],"CLEAR")


if __name__=="__main__":
    unittest.main()
