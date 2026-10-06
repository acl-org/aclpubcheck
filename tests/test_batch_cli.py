"""The aclpubcheck command in batch mode, run as a subprocess the way a user runs it."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import unittest
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import IO
from unittest.mock import patch

import yaml
from batch_fixtures import EXPECTED, entry, write_sample
from pdf_fixtures import REFERENCES, TEXT, write_pages, write_pdf

from aclpubcheck.batch.model import PaperRecord, PaperResult
from aclpubcheck.batch.runner import RunOptions
from aclpubcheck.batch.summary import COLUMNS
from aclpubcheck.formatchecker import main

ACLPUBCHECK = str(Path(sysconfig.get_path("scripts")) / "aclpubcheck")
OUTPUT = "aclpubcheck-batch"  # the default --output-dir, relative to the working directory
SAMPLE_IDS = ["1", "2", "3", "4", "5", "6", "7", "8", "8", "12", "13"]
# ConsoleSink pads the count to the width of the total: "[  1/150] ..."
FIRST_PROGRESS = re.compile(r"\[\s*1/\d+\] ")


def aclpubcheck(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [ACLPUBCHECK, *args],
        cwd=cwd,
        env={**os.environ, "TMPDIR": str(cwd)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def read_rows(path: Path, delimiter: str = ",") -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf8") as stream:
        return list(csv.DictReader(stream, delimiter=delimiter))


def write_manifest(path: Path, entries: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(entries, allow_unicode=True), encoding="utf8")


def write_single(
    root: Path, title: str | None = None, authors: list[dict[str, str]] | None = None
) -> None:
    """papers.yml with one clean paper, id 1, optionally with its own title and authors."""
    (root / "papers").mkdir()
    write_pdf(root / "papers" / "1.pdf", TEXT)
    write_manifest(root / "papers.yml", [entry(1, "1.pdf", title=title, authors=authors)])


def report_prefix(file: str) -> str:
    """The <n> of errors-<n>.json, derived from the PDF name the way Formatter does."""
    return Path(file).name.split("_")[0].removesuffix(".pdf")


class PapersYmlTest(unittest.TestCase):
    """One run over the offline sample, shared by the checks below."""

    root: Path
    result: subprocess.CompletedProcess[str]

    @classmethod
    def setUpClass(cls) -> None:
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.root = Path(directory.name)
        write_sample(cls.root)
        cls.result = aclpubcheck(cls.root, "--papers-yml", "papers.yml")

    def rows(self) -> list[dict[str, str]]:
        return read_rows(self.root / OUTPUT / "summary.csv")

    def test_every_entry_gets_its_expected_row(self) -> None:
        output = self.result.stdout + self.result.stderr
        self.assertEqual(self.result.returncode, 0, output)
        self.assertNotIn("Traceback", output)
        self.assertIn("== finished in", self.result.stderr)
        rows = self.rows()
        self.assertEqual([row["paper_id"] for row in rows], SAMPLE_IDS)
        for row in rows:
            self.assertEqual(row["status"], EXPECTED[row["paper_id"]], row)

    def test_input_snapshot_lists_every_entry(self) -> None:
        snapshot = json.loads((self.root / OUTPUT / "input.json").read_text(encoding="utf8"))
        self.assertEqual(snapshot["source"]["papers_yml"], "papers.yml")
        self.assertEqual([record["paper_id"] for record in snapshot["records"]], SAMPLE_IDS)

    def test_reports_exist_where_expected(self) -> None:
        rows = self.rows()
        for row in rows:
            with self.subTest(paper=row["paper_id"], status=row["status"]):
                if row["status"] in ("missing_file", "invalid_input"):
                    self.assertEqual(row["report_dir"], "")
                    continue
                report_dir = self.root / row["report_dir"]
                log = (report_dir / "check.log").read_text(encoding="utf8")
                self.assertIn("Checking", log)
                if row["status"] == "check_error":
                    self.assertIn("Traceback", log)
                    continue
                prefix = report_prefix(row["file"])
                errors = json.loads((report_dir / f"errors-{prefix}.json").read_text())
                if row["status"] == "passed":
                    self.assertEqual(errors, {})
                elif "Margin" in row["error_categories"]:
                    self.assertIn("Error.MARGIN", errors)
                    self.assertTrue((report_dir / f"errors-{prefix}-page-1.png").is_file())
                else:
                    self.assertIn("Error.PAGELIMIT", errors)
        # 12_camera.pdf and 12_old.pdf both write errors-12.*, each in its own directory
        report_dirs = {row["paper_id"]: row["report_dir"] for row in rows}
        self.assertNotEqual(report_dirs["12"], report_dirs["13"])

    def test_worker_output_stays_in_check_logs(self) -> None:
        for stream in (self.result.stdout, self.result.stderr):
            self.assertNotIn("Checking", stream)
            self.assertNotIn("All Clear", stream)
        for row in self.rows():
            if row["status"] == "passed":
                log = (self.root / row["report_dir"] / "check.log").read_text(encoding="utf8")
                self.assertIn("All Clear!", log)


class OptionsTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_tsv_summary_and_papers_dir(self) -> None:
        # without --papers-dir every entry would be missing_file: there is no papers/ next to it
        manifest = self.root / "manifest" / "accepted.yml"
        manifest.parent.mkdir()
        write_sample(self.root / "data").rename(manifest)
        result = aclpubcheck(
            self.root,
            "--papers-yml",
            "manifest/accepted.yml",
            "--papers-dir",
            "data/papers",
            "--summary",
            "out.tsv",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        header = (self.root / "out.tsv").read_text(encoding="utf8").splitlines()[0]
        self.assertEqual(header.split("\t"), list(COLUMNS))
        rows = read_rows(self.root / "out.tsv", delimiter="\t")
        self.assertEqual([row["paper_id"] for row in rows], SAMPLE_IDS)
        for row in rows:
            self.assertEqual(row["status"], EXPECTED[row["paper_id"]], row)
            self.assertEqual(row["source"], "manifest/accepted.yml")  # the file actually read
        self.assertFalse((self.root / OUTPUT / "summary.csv").exists())

    def test_temp_output_dir(self) -> None:
        write_sample(self.root)
        result = aclpubcheck(self.root, "--papers-yml", "papers.yml", "--temp-output-dir")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # the helper points TMPDIR at the test directory
        (output,) = self.root.glob("aclpubcheck-*")
        self.assertIn(f"summary: {output / 'summary.csv'}", result.stderr)
        self.assertEqual(len(read_rows(output / "summary.csv")), len(SAMPLE_IDS))
        self.assertFalse((self.root / OUTPUT).exists())

    def test_empty_papers_yml_writes_a_header_only_summary(self) -> None:
        (self.root / "papers.yml").write_text("[]\n", encoding="utf8")
        result = aclpubcheck(self.root, "--papers-yml", "papers.yml")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        summary = (self.root / OUTPUT / "summary.csv").read_text(encoding="utf8")
        self.assertEqual(summary.splitlines(), [",".join(COLUMNS)])

    def test_mapping_papers_yml_is_a_usage_error(self) -> None:
        (self.root / "papers.yml").write_text("papers:\n  - id: 1\n", encoding="utf8")
        result = aclpubcheck(self.root, "--papers-yml", "papers.yml")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("expected a list of papers", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_summary_cells_cannot_start_a_formula(self) -> None:
        title = '=HYPERLINK("x")'
        write_single(self.root, title=title, authors=[{"name": "+cmd"}])
        result = aclpubcheck(self.root, "--papers-yml", "papers.yml")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (row,) = read_rows(self.root / OUTPUT / "summary.csv")
        self.assertEqual(row["title"], "'" + title)
        self.assertEqual(row["authors"], "'+cmd")


class UsageErrorTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        write_manifest(self.root / "papers.yml", [entry(1, "1.pdf")])

    def assert_usage_error(self, message: str, *args: str) -> None:
        result = aclpubcheck(self.root, *args)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((self.root / OUTPUT).exists())

    def test_no_inputs(self) -> None:
        self.assert_usage_error("give PDF files or directories")

    def test_paths_with_papers_yml(self) -> None:
        self.assert_usage_error("cannot be combined", "paper.pdf", "--papers-yml", "papers.yml")

    def test_batch_options_in_path_mode(self) -> None:
        write_pdf(self.root / "paper.pdf", TEXT)
        self.assert_usage_error(
            "--summary, --check-references only apply with --papers-yml",
            "paper.pdf",
            "--check-references",
            "--summary",
            "out.csv",
        )

    def test_unwritable_output_dir(self) -> None:
        write_sample(self.root)
        (self.root / "taken").write_text("", encoding="utf8")
        self.assert_usage_error(
            "taken is not a directory", "--papers-yml", "papers.yml", "-o", "taken/out"
        )

    def test_paper_type_in_batch_mode(self) -> None:
        write_sample(self.root)
        self.assert_usage_error(
            "-p/--paper_type does not apply to batch checks",
            "--papers-yml",
            "papers.yml",
            "-p",
            "short",
        )

    def test_missing_papers_yml(self) -> None:
        self.assert_usage_error("cannot read papers", "--papers-yml", "missing.yml")

    def test_unparseable_papers_yml(self) -> None:
        (self.root / "papers.yml").write_text("- id: 1\n  title: [unclosed\n", encoding="utf8")
        self.assert_usage_error("cannot read papers", "--papers-yml", "papers.yml")


class StderrReader(threading.Thread):
    """Drains the child's stderr so it never blocks on a full pipe; notes the first progress."""

    def __init__(self, stream: IO[str]) -> None:
        super().__init__(daemon=True)
        self.stream = stream
        self.lines: list[str] = []
        self.progressed = threading.Event()

    def run(self) -> None:
        for line in self.stream:
            self.lines.append(line)
            if FIRST_PROGRESS.match(line):
                self.progressed.set()


@unittest.skipIf(sys.platform == "win32", "SIGINT cannot target one process on Windows")
class InterruptTest(unittest.TestCase):
    COPIES = 150  # 11-page papers: several seconds of checking with one worker

    def test_sigint_cancels_and_keeps_every_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            papers = root / "papers"
            papers.mkdir()
            write_pages(papers / "0.pdf", [TEXT] * 10 + [REFERENCES])
            for number in range(1, self.COPIES):
                shutil.copyfile(papers / "0.pdf", papers / f"{number}.pdf")
            write_manifest(root / "papers.yml", [entry(n, f"{n}.pdf") for n in range(self.COPIES)])
            with subprocess.Popen(
                [ACLPUBCHECK, "--papers-yml", "papers.yml", "--num_workers", "1"],
                cwd=root,
                env={**os.environ, "TMPDIR": str(root)},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            ) as process:
                assert process.stderr is not None
                reader = StderrReader(process.stderr)
                reader.start()
                try:
                    if not reader.progressed.wait(120):
                        self.fail("no progress line:\n" + "".join(reader.lines))
                    process.send_signal(signal.SIGINT)
                    signalled = time.monotonic()
                    try:
                        code = process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        self.fail("still running 30 s after SIGINT:\n" + "".join(reader.lines))
                    stopped = time.monotonic() - signalled
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                reader.join(10)
            stderr = "".join(reader.lines)
            rows = read_rows(root / OUTPUT / "summary.csv")
        self.assertEqual(code, 130, stderr)
        self.assertLess(stopped, 30)
        self.assertIn("== cancelled after", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertNotIn("never retrieved", stderr)
        self.assertEqual([row["paper_id"] for row in rows], [str(n) for n in range(self.COPIES)])
        statuses = Counter(row["status"] for row in rows)
        self.assertFalse({"queued", "checking"} & statuses.keys(), statuses)
        self.assertGreater(statuses["cancelled"], 0, statuses)
        self.assertGreater(sum(statuses.values()) - statuses["cancelled"], 0, statuses)


class PathModeTest(unittest.TestCase):
    def test_paper_abbreviation_still_works(self) -> None:
        # --papers-yml and --papers-dir would otherwise make "--paper" ambiguous
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_pdf(root / "1234_clean.pdf", TEXT)
            result = aclpubcheck(root, "--paper", "long", "1234_clean.pdf")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("All Clear!", result.stdout)


class NameCheckWiringTest(unittest.TestCase):
    """The name check uploads every PDF of the volume, so batch mode runs it only on request."""

    def name_check(self, *flags: str) -> bool:
        seen: list[RunOptions] = []

        async def run_batch(
            records: Sequence[PaperRecord],
            provider: object,
            options: RunOptions,
            sink: object,
            cancel: object = None,
        ) -> tuple[PaperResult, ...]:
            seen.append(options)
            return tuple(PaperResult(record) for record in records)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_sample(root)
            argv = [
                "aclpubcheck",
                "--papers-yml",
                str(root / "papers.yml"),
                "-o",
                str(root / "out"),
            ]
            with (
                patch.object(sys, "argv", [*argv, *flags]),
                patch("aclpubcheck.batch.cli.run_batch", run_batch),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(main(), 0)
        return seen[0].check.name_check

    def test_offline_reference_checks_do_not_run_it(self) -> None:
        self.assertFalse(self.name_check("--check-references"))
        self.assertFalse(self.name_check("--check-references", "offline"))

    def test_online_reference_checks_run_it(self) -> None:
        self.assertTrue(self.name_check("--check-references", "online"))

    def test_disable_name_check_wins(self) -> None:
        self.assertFalse(self.name_check("--check-references", "online", "--disable_name_check"))


if __name__ == "__main__":
    unittest.main()
