"""Offline sanity tests for official training-set inventory. Never contacts NASA/NRL."""
from __future__ import annotations
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from inventory_official_comet_sets import inspect_archive, DATASETS, BASE


class OfficialInventoryTest(unittest.TestCase):
    def test_whitelist_is_specific(self):
        self.assertEqual(len(DATASETS),6)
        self.assertTrue(BASE.startswith("https://sungrazer.nrl.navy.mil/"))
        self.assertTrue(all(x.startswith("soho") for x in DATASETS))

    def test_nested_gif_and_comet_position_text(self):
        with tempfile.TemporaryDirectory() as t:
            zpath=Path(t)/"practice.zip"
            with zipfile.ZipFile(zpath,"w") as z:
                z.writestr("training/c3/20081016_1106_c3_1024.gif",b"GIF89a")
                z.writestr("training/positions.txt","1106: 640 730\n1118: 637 727\n")
            with patch("builtins.print") as p:
                inspect_archive(zpath)
            output="\n".join(" ".join(map(str,args)) for args,_ in p.call_args_list)
            self.assertIn("Images: 1",output)
            self.assertIn("positions.txt",output)
            self.assertIn("640 730",output)


if __name__ == "__main__":
    unittest.main()
