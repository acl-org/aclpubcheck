"""Performance of batch checking, and that it does not change any result.

The default tests assert behaviour rather than timings, so they hold on any machine: the
name-check database is not built per paper, N workers really run N checks at once, and
batch reports equal those of checking each PDF on its own.

ACLPUBCHECK_TEST_PAPERS_YML (and ACLPUBCHECK_TEST_PAPERS_DIR if the PDFs are not in papers/
next to it) adds the same comparison and a timed one on real papers. The SIGDIAL workflow
sets it; otherwise those tests are skipped.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sysconfig
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from batch_fixtures import (
    BARRIER_ENV,
    DATA,
    Recorder,
    wait_for_peers,
    write_fixture_papers,
    write_sample,
)
from pdf_fixtures import TEXT, write_pdf

from aclpubcheck.batch.check import CheckJob, _name_check, run_check
from aclpubcheck.batch.manifest import LocalPdfProvider, load_papers_yml
from aclpubcheck.batch.model import PaperRecord, Status
from aclpubcheck.batch.runner import RunOptions, run_batch
from aclpubcheck.formatchecker import Formatter, paper_id, report_names

ACLPUBCHECK = str(Path(sysconfig.get_path("scripts")) / "aclpubcheck")
CHECKED = {Status.PASSED.value, Status.WARNINGS.value, Status.VIOLATIONS.value}


def aclpubcheck(cwd: Path, *args: str) -> None:
    subprocess.run(
        [ACLPUBCHECK, *args],
        cwd=cwd,
        env={**os.environ, "TMPDIR": str(cwd)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=600,
    )


def batch_reports(papers_yml: Path, papers_dir: Path, out: Path, workers: int) -> dict[Path, Path]:
    """Run batch mode; the JSON report of every PDF that was checked to the end."""
    aclpubcheck(
        out.parent,
        "--papers-yml",
        str(papers_yml),
        "--papers-dir",
        str(papers_dir),
        "--num_workers",
        str(workers),
        "-o",
        str(out),
    )
    with (out / "summary.csv").open(newline="", encoding="utf8") as stream:
        rows = [row for row in csv.DictReader(stream) if row["status"] in CHECKED]
    return {
        Path(row["file"]).resolve(): Path(row["report_dir"])
        / f"errors-{paper_id(row['file'])}.json"
        for row in rows
    }


def path_mode_reports(pdfs: dict[Path, str], out: Path) -> dict[Path, Path]:
    """Check the PDFs as a chair would without batch mode: one run per paper type."""
    out.mkdir(parents=True)
    reports = {}
    for paper_type in sorted(set(pdfs.values())):
        files = sorted(str(pdf) for pdf, kind in pdfs.items() if kind == paper_type)
        directory = out / paper_type
        aclpubcheck(out, "-p", paper_type, "-o", str(directory), *files)
        for pdf, name in zip(files, report_names(files)):
            reports[Path(pdf)] = directory / f"errors-{name}.json"
    return reports


class Equivalence:
    """Batch reports must equal the reports of checking each PDF on its own."""

    def assert_same_reports(self: unittest.TestCase, papers_yml: Path, papers_dir: Path) -> None:
        types = {
            (papers_dir / r.file).resolve(): r.paper_type
            for r in load_papers_yml(papers_yml)
            if r.paper_type and not r.problems
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = batch_reports(papers_yml, papers_dir, root / "batch", workers=2)
            self.assertTrue(batch, "no paper was checked")
            path_mode = path_mode_reports({pdf: types[pdf] for pdf in batch}, root / "path")
            for pdf, report in batch.items():
                with self.subTest(pdf=pdf.name):
                    self.assertEqual(
                        json.loads(report.read_text(encoding="utf8")),
                        json.loads(path_mode[pdf].read_text(encoding="utf8")),
                    )


class EquivalenceTest(Equivalence, unittest.TestCase):
    def test_offline_sample(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            papers_yml = write_sample(directory)
            self.assert_same_reports(papers_yml, papers_yml.parent / "papers")

    def test_committed_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assert_same_reports(DATA / "papers.yml", write_fixture_papers(directory))


class NameCheckTest(unittest.TestCase):
    """Building the rebiber database takes seconds and about 1 GB; every paper used to pay it."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.pdfs = [self.root / f"{n}.pdf" for n in range(3)]
        for pdf in self.pdfs:
            write_pdf(pdf, TEXT)
        _name_check.cache_clear()
        self.addCleanup(_name_check.cache_clear)

    def test_format_checks_do_not_build_it(self) -> None:
        with (
            patch("aclpubcheck.formatchecker.PDFNameCheck") as path_mode,
            patch("aclpubcheck.batch.check.PDFNameCheck") as batch,
        ):
            for pdf in self.pdfs:
                run_check(CheckJob(pdf, "long", self.root / "reports" / pdf.stem))
                with patch("sys.stdout"):
                    Formatter().format_check(str(pdf), "long", output_dir=str(self.root))
        path_mode.assert_not_called()
        batch.assert_not_called()

    def test_reference_checks_build_it_once_per_process(self) -> None:
        database = MagicMock()
        database.return_value.execute.return_value = []  # no network call to the name service
        with patch("aclpubcheck.batch.check.PDFNameCheck", database):
            for pdf in self.pdfs:
                outcome = run_check(
                    CheckJob(pdf, "long", self.root / "reports" / pdf.stem, check_references=True)
                )
                self.assertEqual(outcome.status, Status.WARNINGS, outcome)
        database.assert_called_once()


class ConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.records = [
            PaperRecord(index=n, paper_id=str(n), paper_type="long", file=f"{n}.pdf")
            for n in range(1, 7)
        ]
        for record in self.records:
            write_pdf(self.root / record.file, TEXT)

    async def test_workers_run_checks_at_the_same_time(self) -> None:
        workers = 3
        barrier = self.root / "barrier"
        barrier.mkdir()
        with (
            patch.dict(os.environ, {BARRIER_ENV: f"{barrier}:{workers}"}),
            patch("aclpubcheck.batch.runner.run_check", wait_for_peers),
        ):
            results = await run_batch(
                self.records,
                LocalPdfProvider(self.root),
                RunOptions(report_root=self.root / "reports", num_workers=workers),
                Recorder(),
            )
        self.assertEqual([r.status for r in results], [Status.PASSED] * len(self.records))
        self.assertEqual(len(list(barrier.iterdir())), len(self.records))  # every check waited


@unittest.skipUnless(
    os.environ.get("ACLPUBCHECK_TEST_PAPERS_YML"),
    "set ACLPUBCHECK_TEST_PAPERS_YML to use real papers",
)
class RealPapersTest(Equivalence, unittest.TestCase):
    def setUp(self) -> None:
        self.papers_yml = Path(os.environ["ACLPUBCHECK_TEST_PAPERS_YML"]).resolve()
        papers_dir = os.environ.get("ACLPUBCHECK_TEST_PAPERS_DIR")
        self.papers_dir = (
            Path(papers_dir).resolve() if papers_dir else self.papers_yml.parent / "papers"
        )

    def test_reports_match_path_mode(self) -> None:
        self.assert_same_reports(self.papers_yml, self.papers_dir)

    def test_more_workers_are_faster(self) -> None:
        affinity = getattr(os, "sched_getaffinity", None)  # the CPUs this process may use
        if (len(affinity(0)) if affinity else os.cpu_count() or 1) < 4:
            self.skipTest("needs at least 4 CPUs")
        seconds = {}
        with tempfile.TemporaryDirectory() as directory:
            for count in (1, 4):
                started = time.perf_counter()
                batch_reports(self.papers_yml, self.papers_dir, Path(directory) / str(count), count)
                seconds[count] = time.perf_counter() - started
        print(
            f"\nbatch check of {self.papers_yml}: "
            + ", ".join(f"{n} worker(s) {s:.1f} s" for n, s in seconds.items())
        )
        # checks are CPU-bound and independent, so 4 workers run them about 2.2x as fast on
        # the GitHub runner; checking one paper at a time would be about 1x
        self.assertGreater(seconds[1] / seconds[4], 1.5, seconds)


if __name__ == "__main__":
    unittest.main()
