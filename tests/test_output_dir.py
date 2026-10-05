from argparse import Namespace
from contextlib import redirect_stdout
import gc
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sysconfig
import tempfile
import unicodedata
import unittest
import warnings

from aclpubcheck import formatchecker
from aclpubcheck.formatchecker import Formatter, report_names
from pdf_fixtures import MARGIN_TEXT, TEXT, write_pdf


def names(directory):
    return sorted(path.name for path in directory.iterdir())


def report(path):
    return json.loads(path.read_text())


def content_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:8]


class OutputDirTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.papers = self.root / "papers"
        self.cwd = self.root / "cwd"
        self.tmp = self.root / "tmp"
        for path in (self.papers, self.cwd, self.tmp):
            path.mkdir()

    def pdf(self, name, content):
        path = self.papers / name
        path.parent.mkdir(parents=True, exist_ok=True)
        write_pdf(path, content)
        return path

    def run_cli(self, *args):
        papers = sorted(self.papers.rglob("*"))
        result = subprocess.run(
            [str(Path(sysconfig.get_path("scripts")) / "aclpubcheck"), "-p", "long", *map(str, args)],
            cwd=self.cwd,
            # unbuffered, so the workers' output survives the pool terminating them
            env={**os.environ, "PYTHONUNBUFFERED": "1", "TMPDIR": str(self.tmp)},
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        self.assertEqual(sorted(self.papers.rglob("*")), papers)
        return result.stdout

    def test_default_output_dir_is_working_directory(self):
        # the Hugging Face Space looks for errors-<id>*.png in its working directory
        stdout = self.run_cli(self.pdf("1234_margin.pdf", MARGIN_TEXT))
        self.assertIn("Errors. Check errors-1234.json for details.", stdout)
        self.assertNotIn("Saving reports to", stdout)
        self.assertNotIn("Reports saved to", stdout)
        self.assertEqual(names(self.cwd), ["errors-1234-page-1.png", "errors-1234.json"])
        self.assertEqual(list(report(self.cwd / "errors-1234.json")), ["Error.MARGIN"])
        self.assertEqual(names(self.tmp), [])

    def test_unique_ids_keep_their_names(self):
        self.pdf("1234_clean.pdf", TEXT)
        self.pdf("5678_margin.pdf", MARGIN_TEXT)
        self.run_cli(self.papers)
        self.assertEqual(
            names(self.cwd), ["errors-1234.json", "errors-5678-page-1.png", "errors-5678.json"]
        )
        self.assertEqual(report(self.cwd / "errors-1234.json"), {})

    def test_same_id_in_one_run(self):
        self.pdf("1234_clean.pdf", TEXT)
        self.pdf("1234_margin.pdf", MARGIN_TEXT)
        for workers in ("1", "2"):
            with self.subTest(num_workers=workers):
                output_dir = self.root / f"workers-{workers}"
                stdout = self.run_cli("--num_workers", workers, "-o", output_dir, self.papers)
                self.assertNotIn("Parsing Error", stdout)
                self.assertIn(f"Errors. Check {output_dir / 'errors-1234_margin.json'} for details.", stdout)
                self.assertEqual(
                    names(output_dir),
                    ["errors-1234_clean.json", "errors-1234_margin-page-1.png", "errors-1234_margin.json"],
                )
                self.assertEqual(report(output_dir / "errors-1234_clean.json"), {})
                self.assertEqual(list(report(output_dir / "errors-1234_margin.json")), ["Error.MARGIN"])

    def test_path_spellings_of_one_pdf(self):
        self.pdf("1234_margin.pdf", MARGIN_TEXT)
        (self.papers / "sub").mkdir()
        link = self.root / "link"
        link.symlink_to(self.papers / "sub")
        stdout = self.run_cli(
            self.papers, f"{self.papers}/.", f"{self.papers}//1234_margin.pdf", f"{link}/../1234_margin.pdf"
        )
        self.assertEqual(stdout.count("Checking "), 1)
        self.assertEqual(names(self.cwd), ["errors-1234-page-1.png", "errors-1234.json"])

    def test_same_file_name_in_two_directories(self):
        clean = f"errors-1234_paper_{content_hash(self.pdf('a/1234_paper.pdf', TEXT))}"
        margin = f"errors-1234_paper_{content_hash(self.pdf('b/1234_paper.pdf', MARGIN_TEXT))}"
        self.run_cli(self.papers)
        self.assertEqual(names(self.cwd), sorted([f"{clean}.json", f"{margin}-page-1.png", f"{margin}.json"]))
        self.assertEqual(report(self.cwd / f"{clean}.json"), {})
        self.assertEqual(list(report(self.cwd / f"{margin}.json")), ["Error.MARGIN"])

    def test_later_run_overwrites_same_names(self):
        self.run_cli("-o", "reports", self.pdf("1234_margin.pdf", MARGIN_TEXT))
        self.assertEqual(list(report(self.cwd / "reports" / "errors-1234.json")), ["Error.MARGIN"])
        self.run_cli("-o", "reports", self.pdf("1234_clean.pdf", TEXT))
        self.assertEqual(report(self.cwd / "reports" / "errors-1234.json"), {})
        # files that the later run does not write are left alone
        self.assertEqual(names(self.cwd / "reports"), ["errors-1234-page-1.png", "errors-1234.json"])

    def test_explicit_output_dir(self):
        margin = self.pdf("1234_margin.pdf", MARGIN_TEXT)
        for flag, output_dir, expected in (
            ("-o", Path("relative/nested"), self.cwd / "relative" / "nested"),
            ("--output-dir", self.root / "absolute" / "nested", self.root / "absolute" / "nested"),
        ):
            with self.subTest(flag=flag):
                stdout = self.run_cli(flag, output_dir, margin)
                self.assertTrue(stdout.startswith(f"Saving reports to {output_dir}\n"), stdout)
                self.assertTrue(stdout.endswith(f"Reports saved to {output_dir}\n"), stdout)
                self.assertEqual(names(expected), ["errors-1234-page-1.png", "errors-1234.json"])
        self.assertEqual(names(self.cwd), ["relative"])
        self.assertEqual(names(self.tmp), [])

    def test_explicit_current_directory(self):
        stdout = self.run_cli("-o", ".", self.pdf("1234_margin.pdf", MARGIN_TEXT))
        self.assertIn("Saving reports to .", stdout)
        self.assertIn("Errors. Check errors-1234.json for details.", stdout)
        self.assertEqual(names(self.cwd), ["errors-1234-page-1.png", "errors-1234.json"])

    def test_temp_output_dir(self):
        clean = self.pdf("1234_clean.pdf", TEXT)
        report_dirs = []
        for _ in range(2):
            before = set(self.tmp.iterdir())
            stdout = self.run_cli("--temp-output-dir", clean)
            (report_dir,) = set(self.tmp.iterdir()) - before
            self.assertTrue(report_dir.name.startswith("aclpubcheck-"))
            self.assertTrue(stdout.startswith(f"Saving reports to {report_dir}\n"), stdout)
            self.assertTrue(stdout.endswith(f"Reports saved to {report_dir}\n"), stdout)
            self.assertEqual(names(report_dir), ["errors-1234.json"])
            self.assertEqual(report(report_dir / "errors-1234.json"), {})
            report_dirs.append(report_dir)
        self.assertEqual(names(report_dirs[0]), ["errors-1234.json"])
        self.assertEqual(names(self.cwd), [])

    def test_output_dir_options_are_exclusive(self):
        result = subprocess.run(
            [str(Path(sysconfig.get_path("scripts")) / "aclpubcheck"), "-o", "reports", "--temp-output-dir", "x.pdf"],
            cwd=self.cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("not allowed with argument", result.stderr)

    def test_no_pdfs_creates_no_temp_dir(self):
        stdout = self.run_cli("--temp-output-dir", self.papers)
        self.assertIn("No PDF files found", stdout)
        self.assertEqual(names(self.tmp), [])


class ReportNamesTest(unittest.TestCase):
    def test_names(self):
        for pdfs, expected in (
            (["p/1234_a.pdf", "p/5678_b.pdf", "p/paper.pdf"], ["1234", "5678", "paper"]),
            (["p/1234.pdf", "p/1234_b.pdf"], ["1234", "1234_b"]),
            # names that differ only in case would overwrite each other on some file systems
            (["p/ABC_x.pdf", "p/abc_y.pdf"], ["ABC_x", "abc_y"]),
        ):
            with self.subTest(pdfs=pdfs):
                self.assertEqual(report_names(pdfs), expected)

    def test_same_file_names_add_content_hash(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for names_ in (
            ["a/1234.pdf", "b/1234.pdf"],
            ["a/1234_X.pdf", "b/1234_x.pdf"],
            # names that differ only in Unicode normalization are one file on some file systems
            [unicodedata.normalize(form, f"{d}/café.pdf") for d, form in (("a", "NFC"), ("b", "NFD"))],
        ):
            with self.subTest(pdfs=names_):
                pdfs = []
                for content, name in enumerate(names_):
                    pdf = root / str(len(list(root.iterdir()))) / name
                    pdf.parent.mkdir(parents=True)
                    pdf.write_bytes(b"%d" % content)
                    pdfs.append(str(pdf))
                self.assertEqual(
                    report_names(pdfs), [f"{Path(pdf).stem}_{content_hash(pdf)}" for pdf in pdfs]
                )


class LibraryTest(unittest.TestCase):
    def test_format_check_defaults(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        self.addCleanup(setattr, formatchecker, "args", formatchecker.args)
        os.chdir(directory.name)
        formatchecker.args = Namespace(disable_bottom_check=True, disable_name_check=True)
        write_pdf("1234_margin.pdf", MARGIN_TEXT)
        for check in (Formatter().format_check, formatchecker.worker):
            with self.subTest(check=check.__name__):
                with redirect_stdout(io.StringIO()) as stdout, warnings.catch_warnings():
                    # format_check leaves the PDF open
                    warnings.simplefilter("ignore", ResourceWarning)
                    check("1234_margin.pdf", "long")
                    gc.collect()
                self.assertIn("Errors. Check errors-1234.json for details.", stdout.getvalue())
                self.assertEqual(
                    names(Path(directory.name)), ["1234_margin.pdf", "errors-1234-page-1.png", "errors-1234.json"]
                )
                for report_file in ("errors-1234-page-1.png", "errors-1234.json"):
                    os.remove(report_file)


if __name__ == "__main__":
    unittest.main()
