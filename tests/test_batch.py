from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from batch_fixtures import EXPECTED, Recorder, crash_on_marker, semantic, write_sample
from pdf_fixtures import MARGIN_TEXT, TEXT, write_pdf

from aclpubcheck.batch.check import CheckJob, run_check
from aclpubcheck.batch.console import ConsoleSink
from aclpubcheck.batch.manifest import LocalPdfProvider, load_papers_yml
from aclpubcheck.batch.model import (
    Author,
    Event,
    EventSink,
    FetchedPdf,
    PaperChanged,
    PaperRecord,
    PaperResult,
    RunFinished,
    RunStarted,
    Status,
    printable,
)
from aclpubcheck.batch.runner import RunOptions, run_batch
from aclpubcheck.batch.summary import SummaryWriter, summary_row, write_summary
from aclpubcheck.formatchecker import CheckConfig


class ManifestTest(unittest.TestCase):
    def test_entries_are_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = write_sample(directory)
            records = load_papers_yml(manifest)
        by_index = {r.index: r for r in records}
        self.assertEqual({r.source for r in records}, {str(manifest)})
        self.assertEqual(
            [r.paper_id for r in records],
            ["1", "2", "3", "4", "5", "6", "7", "8", "8", "12", "13"],
        )
        self.assertEqual(by_index[1].paper_type, "short")
        self.assertEqual(by_index[0].authors[0].email, "author_1@example.org")
        self.assertIn("unknown paper_type 'N/A'", by_index[6].problems[0])
        self.assertIn("id 8 is used by 2 entries", by_index[7].problems)
        self.assertIn("id 8 is used by 2 entries", by_index[8].problems)
        self.assertIn("no authors listed", by_index[9].notes)
        self.assertFalse(by_index[10].problems)


class RunBatchTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.manifest = write_sample(self.root)
        self.records = load_papers_yml(self.manifest)
        self.provider = LocalPdfProvider(self.root / "papers")

    async def run_sample(
        self,
        workers: int,
        out: str = "out",
        sink: EventSink | None = None,
        check: CheckConfig | None = None,
        check_references: bool = False,
    ) -> tuple[PaperResult, ...]:
        return await run_batch(
            self.records,
            self.provider,
            RunOptions(
                report_root=self.root / out / "reports",
                num_workers=workers,
                check=check or CheckConfig(),
                check_references=check_references,
            ),
            sink or Recorder(),
        )

    async def test_every_expected_paper_ends_with_its_own_report(self) -> None:
        summary = self.root / "out" / "summary.csv"
        results = await self.run_sample(2, sink=SummaryWriter(summary))
        self.assertEqual(len(results), len(self.records))
        for result in results:
            self.assertEqual(result.status.value, EXPECTED[result.record.paper_id], result)
        with summary.open(newline="", encoding="utf8") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(
            [(row["paper_id"], row["status"]) for row in rows],
            [(r.record.paper_id, r.status.value) for r in results],
        )
        self.assertEqual(rows[0]["emails"], "author_1@example.org")
        self.assertIn("Margin", rows[1]["error_categories"])
        self.assertIn("Page Limit", rows[2]["error_categories"])
        self.assertIn("ValueError", rows[4]["details"])
        self.assertIn("not found", rows[5]["details"])

        # 12_camera.pdf and 12_old.pdf both write errors-12.json, each in its own directory
        by_id = {r.record.paper_id: r for r in results}
        clean, margin = by_id["12"], by_id["13"]
        assert clean.report_dir is not None and margin.report_dir is not None
        self.assertNotEqual(clean.report_dir, margin.report_dir)
        self.assertEqual(json.loads((clean.report_dir / "errors-12.json").read_text()), {})
        self.assertIn(
            "Error.MARGIN",
            json.loads((margin.report_dir / "errors-12.json").read_text()),
        )
        self.assertIn("errors-12-page-1.png", margin.report_files)
        self.assertNotIn("errors-12-page-1.png", clean.report_files)
        for result in results:
            if result.report_dir:
                self.assertIn("check.log", result.report_files)

    async def test_serial_and_concurrent_runs_agree(self) -> None:
        serial = await self.run_sample(1, out="serial")
        concurrent = await self.run_sample(3, out="concurrent")
        self.assertEqual(semantic(serial), semantic(concurrent))

    async def test_revised_pdf_keeps_both_reports(self) -> None:
        first = (await self.run_sample(1))[0]
        write_pdf(self.root / "papers" / "1.pdf", MARGIN_TEXT)
        second = (await self.run_sample(1))[0]
        self.assertEqual((first.status, second.status), (Status.PASSED, Status.VIOLATIONS))
        assert first.pdf is not None and second.pdf is not None
        assert first.report_dir is not None and second.report_dir is not None
        self.assertNotEqual(first.pdf.sha256, second.pdf.sha256)
        self.assertNotEqual(first.report_dir, second.report_dir)
        self.assertEqual(json.loads((first.report_dir / "errors-1.json").read_text()), {})
        self.assertIn(
            "Error.MARGIN",
            json.loads((second.report_dir / "errors-1.json").read_text()),
        )

    async def test_warnings_are_not_errors(self) -> None:
        self.records = [r for r in self.records if r.paper_id == "1"]
        result = (
            await self.run_sample(1, check=CheckConfig(name_check=False), check_references=True)
        )[0]
        self.assertEqual(result.status, Status.WARNINGS)
        self.assertFalse(result.errors)
        self.assertEqual({w.category for w in result.warnings}, {"Bibliography"})

    async def test_cancel_marks_unfinished_papers(self) -> None:
        release = asyncio.Event()

        class Stalling:
            async def fetch(inner, record: PaperRecord) -> FetchedPdf:
                if record.paper_id != "1":
                    await release.wait()
                return await self.provider.fetch(record)

        cancel = asyncio.Event()
        recorder = Recorder()
        summary = self.root / "summary.tsv"
        writer = SummaryWriter(summary)

        def sink(event: Event) -> None:
            recorder(event)
            writer(event)
            if (
                isinstance(event, PaperChanged)
                and event.result.record.paper_id == "1"
                and event.result.status.terminal
            ):
                cancel.set()

        results = await run_batch(
            self.records,
            Stalling(),
            RunOptions(report_root=self.root / "reports"),
            sink,
            cancel,
        )
        statuses = {r.record.index: r.status for r in results}
        self.assertEqual(statuses[0], Status.PASSED)
        for record in self.records[1:]:
            expected = Status.INVALID_INPUT if record.problems else Status.CANCELLED
            self.assertEqual(statuses[record.index], expected, record)
        finished = recorder.events[-1]
        assert isinstance(finished, RunFinished)
        self.assertTrue(finished.cancelled)
        with summary.open(newline="", encoding="utf8") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        self.assertEqual(len(rows), len(self.records))
        self.assertTrue(all(Status(row["status"]).terminal for row in rows))

    async def test_cancelling_the_task_still_finishes_every_record(self) -> None:
        recorder = Recorder()

        class Hanging:
            async def fetch(inner, record: PaperRecord) -> FetchedPdf:
                await asyncio.Event().wait()
                raise AssertionError("the event is never set")

        task = asyncio.ensure_future(
            run_batch(
                self.records,
                Hanging(),
                RunOptions(report_root=self.root / "reports"),
                recorder,
            )
        )
        while not any(isinstance(e, PaperChanged) for e in recorder.events):
            await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        finished = recorder.events[-1]
        assert isinstance(finished, RunFinished)
        self.assertTrue(finished.cancelled)
        self.assertTrue(all(r.status.terminal for r in finished.results))

    @unittest.skipIf(sys.platform == "win32" or os.geteuid() == 0, "needs POSIX permissions")
    async def test_unreadable_pdf_does_not_end_the_run(self) -> None:
        unreadable = self.root / "papers" / "2.pdf"
        unreadable.chmod(0)
        self.addCleanup(unreadable.chmod, 0o644)
        results = await self.run_sample(2)
        for result in results:
            expected = (
                "missing_file"
                if result.record.paper_id == "2"
                else EXPECTED[result.record.paper_id]
            )
            self.assertEqual(result.status.value, expected, result)
        self.assertIn("cannot be read", results[1].message)

    async def test_unwritable_reports_end_as_check_errors(self) -> None:
        blocker = self.root / "not-a-directory"
        blocker.write_text("", encoding="utf8")
        results = await run_batch(
            self.records, self.provider, RunOptions(report_root=blocker), Recorder()
        )
        checked = [r for r in results if r.pdf is not None]
        self.assertTrue(checked)
        for result in checked:
            self.assertEqual(result.status, Status.CHECK_ERROR, result)
            self.assertIn("cannot write reports", result.message)
        self.assertFalse([r for r in results if r.status is Status.CANCELLED])

    async def test_very_long_id_is_checked(self) -> None:
        long_id = "x" * 300
        self.records = [
            PaperRecord(
                index=0, paper_id=long_id, paper_type="long", file="1.pdf", source_id=long_id
            )
        ]
        result = (await self.run_sample(1))[0]
        self.assertEqual(result.status, Status.PASSED, result)
        assert result.report_dir is not None
        self.assertLess(len(result.report_dir.name), 80)

    async def test_rerun_replaces_reports_of_the_same_version(self) -> None:
        page_number = TEXT + b"\n1 0 0 rg BT /F1 10 Tf 290 20 Td (1) Tj ET"
        write_pdf(self.root / "papers" / "1.pdf", page_number)
        self.records = self.records[:1]
        first = (await self.run_sample(1))[0]
        second = (await self.run_sample(1, check=CheckConfig(bottom_check=False)))[0]
        self.assertEqual((first.status, second.status), (Status.VIOLATIONS, Status.PASSED))
        self.assertEqual(first.report_dir, second.report_dir)
        self.assertIn("errors-1-page-1.png", first.report_files)
        self.assertEqual(second.report_files, ("check.log", "errors-1.json"))
        assert second.report_dir is not None
        self.assertEqual(
            sorted(p.name for p in second.report_dir.iterdir()), list(second.report_files)
        )

    async def test_a_crashing_check_does_not_blame_the_papers_beside_it(self) -> None:
        papers = self.root / "papers"
        write_pdf(papers / "crash.pdf", TEXT)
        self.records = [
            PaperRecord(index=i, paper_id=str(i), paper_type="long", file=name, source_id=str(i))
            for i, name in enumerate(["1.pdf", "crash.pdf", "12_camera.pdf", "7.pdf"])
        ]
        with patch("aclpubcheck.batch.runner.run_check", crash_on_marker):
            serial = await self.run_sample(1, out="serial")
            parallel = await self.run_sample(4, out="parallel")
        self.assertEqual(semantic(serial), semantic(parallel))
        statuses = [r.status for r in parallel]
        self.assertEqual(
            statuses, [Status.PASSED, Status.CHECK_ERROR, Status.PASSED, Status.PASSED]
        )
        self.assertIn("retried alone", parallel[1].message)

    async def test_started_event_lists_every_record_before_work(self) -> None:
        recorder = Recorder()
        await self.run_sample(1, sink=recorder)
        started = recorder.events[0]
        assert isinstance(started, RunStarted)
        self.assertEqual(len(started.results), len(self.records))
        self.assertTrue(all(r.status is Status.QUEUED for r in started.results))


class SummaryTest(unittest.TestCase):
    def test_tsv_suffix_uses_tabs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.tsv"
            write_summary(path, [PaperResult(PaperRecord(index=0, paper_id="1", title="A, B"))])
            header, row = path.read_text(encoding="utf8").splitlines()
        self.assertTrue(header.startswith("paper_id\tstatus\t"))
        self.assertIn("\tA, B\t", row)

    def test_author_text_cannot_break_rows_or_run_formulas(self) -> None:
        record = PaperRecord(
            index=0,
            paper_id="-5",
            title=' =HYPERLINK("x")\x00\x1b]0;PWNED\x07\nsecond\tline',
            authors=(Author(name="@cmd", email="-1@example.org"),),
            source_id="-2FCwDKRREu",
        )
        row = summary_row(PaperResult(record))
        self.assertEqual(row["paper_id"], "-5")
        self.assertEqual(row["source_id"], "-2FCwDKRREu")
        self.assertEqual(row["title"], '\' =HYPERLINK("x")\ufffd\ufffd]0;PWNED\ufffd second line')
        self.assertEqual((row["authors"], row["emails"]), ("'@cmd", "'-1@example.org"))
        with tempfile.TemporaryDirectory() as directory:
            for name in ("summary.csv", "summary.tsv"):
                path = Path(directory) / name
                write_summary(path, [PaperResult(record)])  # NUL used to crash csv on 3.10
                self.assertEqual(len(path.read_text(encoding="utf8").splitlines()), 2, name)

    def test_printable(self) -> None:
        self.assertEqual(printable("a\tb\r\nc\x00\x1b[2J\x9bd"), "a b  c\ufffd\ufffd[2J\ufffdd")
        self.assertEqual(printable("x\u2028y\u2029z \u202eevil\u2066"), "x y z \ufffdevil\ufffd")

    def test_file_column_is_verbatim(self) -> None:
        row = summary_row(PaperResult(PaperRecord(index=0, paper_id="1", file="-paper.pdf")))
        self.assertEqual(row["file"], "-paper.pdf")

    @unittest.skipIf(sys.platform == "win32", "POSIX permissions")
    def test_summary_gets_umask_permissions(self) -> None:
        umask = os.umask(0o022)
        self.addCleanup(os.umask, umask)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.csv"
            write_summary(path, [])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)


class SummaryWriterTest(unittest.IsolatedAsyncioTestCase):
    async def test_rewrites_are_throttled_but_never_lost(self) -> None:
        record = PaperRecord(index=0, paper_id="1")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.csv"
            writer = SummaryWriter(path, interval=0.2)
            with patch("aclpubcheck.batch.summary.write_summary", wraps=write_summary) as write:
                writer(RunStarted((PaperResult(record),)))
                for status in (Status.CHECKING, Status.QUEUED, Status.CHECKING, Status.PASSED):
                    writer(PaperChanged(PaperResult(record, status=status)))
                self.assertEqual(write.call_count, 1)  # only the start so far
                self.assertIn("queued", path.read_text(encoding="utf8"))
                await asyncio.sleep(0.4)  # the scheduled write catches up without a new event
                self.assertEqual(write.call_count, 2)
                self.assertIn("passed", path.read_text(encoding="utf8"))
                writer(RunFinished((PaperResult(record, status=Status.PASSED),), cancelled=False))
                self.assertEqual(write.call_count, 3)


class RunCheckTest(unittest.TestCase):
    def test_changed_pdf_is_not_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "1.pdf"
            write_pdf(pdf, TEXT)
            job = CheckJob(pdf, "long", Path(directory) / "report", sha256="0" * 64)
            outcome = run_check(job)
        self.assertEqual(outcome.status, Status.CHECK_ERROR)
        self.assertIn("changed after it was hashed", outcome.message)


class ConsoleTest(unittest.TestCase):
    def test_escape_sequences_are_not_printed(self) -> None:
        stream = io.StringIO()
        sink = ConsoleSink(stream)
        record = PaperRecord(index=0, paper_id="1\x1b]0;PWNED\x07")
        sink(RunStarted((PaperResult(record),)))
        sink(PaperChanged(PaperResult(record, status=Status.CHECK_ERROR, message="bad\x1b[2J")))
        self.assertNotIn("\x1b", stream.getvalue())
        self.assertIn("check_error", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
