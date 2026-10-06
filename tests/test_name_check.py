"""PDFNameCheck keeps the files of each call apart, so concurrent batch workers never mix them."""

import os
import shlex
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aclpubcheck.name_check import PDFNameCheck


class TempFilesTest(unittest.TestCase):
    def test_each_call_has_its_own_directory_and_removes_it(self) -> None:
        checker = PDFNameCheck.__new__(PDFNameCheck)  # skips building the rebiber database
        commands: list[str] = []
        directories: list[str] = []

        def curl(command: str) -> int:
            commands.append(command)
            directories.append(checker.temp_dir)
            return 0

        # before, every paper under a directory with a dot in its name shared temp/before-rebiber-vol.bib
        config = SimpleNamespace(file="/data/vol.2024/papers/1.pdf", ref_string="", mode="")
        with (
            patch("aclpubcheck.name_check.os.system", curl),
            patch.object(PDFNameCheck, "apply_rebiber"),
            patch.object(PDFNameCheck, "extract_names", return_value={}),
            patch.object(PDFNameCheck, "compare_changes", return_value=[]),
        ):
            checker.execute(config)
            checker.execute(config)
        self.assertNotEqual(directories[0], directories[1])
        for command, directory in zip(commands, directories):
            before = shlex.quote(os.path.join(directory, "before-rebiber.bib"))
            self.assertTrue(command.endswith(f"> {before}"), command)
            self.assertFalse(os.path.exists(directory))


if __name__ == "__main__":
    unittest.main()
