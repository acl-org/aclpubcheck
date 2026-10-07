import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pybtex.database import parse_file

from aclpubcheck.name_check import PDFNameCheck


class RebiberTest(unittest.TestCase):
    def test_normalize_and_extract_names(self):
        checker = PDFNameCheck()
        self.assertTrue(checker.bib_db)
        with tempfile.TemporaryDirectory() as directory:
            checker.temp_dir = directory
            Path(directory, "before-rebiber.bib").write_text(
                "@article{lin2020birds,\n"
                " title={Birds have four legs?! NumerSense: Probing Numerical "
                "Commonsense Knowledge of Pre-Trained Language Models},\n"
                " author={Lin, Bill Yuchen and Lee, Seyeon and Khanna, Rahul and Ren, Xiang},\n"
                " journal={arXiv preprint arXiv:2005.00683},\n"
                " year={2020}\n}\n",
                encoding="utf8",
            )
            with patch(
                "urllib.request.urlopen",
                side_effect=AssertionError("Unexpected network request"),
            ):
                checker.apply_rebiber()
            entry = parse_file(os.path.join(directory, "after-rebiber.bib")).entries["lin2020birds"]
            self.assertEqual(entry.type, "inproceedings")
            self.assertIn(
                "Empirical Methods in Natural Language Processing", entry.fields["booktitle"]
            )
            names = checker.extract_names()["lin2020birds"]
            self.assertEqual(len(names["old"]), 4)
            self.assertEqual(len(names["new"]), 4)
            self.assertIn("aclanthology.org", names["url"])

if __name__ == "__main__":
    unittest.main()
