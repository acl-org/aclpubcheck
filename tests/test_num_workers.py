import os
from pathlib import Path
import subprocess
import sysconfig
import tempfile
import unittest

from pdf_fixtures import MARGIN_TEXT, TEXT, write_pdf


class NumWorkersTest(unittest.TestCase):
    def test_parallel_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            papers = Path(directory) / "papers"
            papers.mkdir()
            write_pdf(papers / "1234_clean.pdf", TEXT)
            write_pdf(papers / "5678_margin.pdf", MARGIN_TEXT)
            result = subprocess.run(
                [
                    str(Path(sysconfig.get_path("scripts")) / "aclpubcheck"),
                    "-p",
                    "long",
                    "--num_workers",
                    "2",
                    str(papers),
                ],
                cwd=directory,
                # unbuffered, so the workers' output survives the pool terminating them;
                # TMPDIR keeps any reports inside the scratch directory
                env={**os.environ, "PYTHONUNBUFFERED": "1", "TMPDIR": directory},
                capture_output=True,
                text=True,
                timeout=120,
            )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertNotIn("Traceback", output)
        self.assertNotIn("Parsing Error", output)
        self.assertEqual(result.stdout.count("All Clear!"), 1, output)
        self.assertIn("Text on page 1 bleeds into the right margin.", result.stdout)


if __name__ == "__main__":
    unittest.main()
