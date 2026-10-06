"""Batch mode of the aclpubcheck command: --papers-yml."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import tempfile
from contextlib import suppress
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from ..formatchecker import CheckConfig
from .console import ConsoleSink
from .manifest import LocalPdfProvider, ManifestError, load_papers_yml
from .model import (
    EventSink,
    Job,
    PaperRecord,
    PaperResult,
    PdfProvider,
    StageChanged,
    Status,
    fan_out,
)
from .runner import RunOptions, run_batch
from .summary import SummaryWriter, write_summary

EXIT_NO_PAPERS = 1
EXIT_USAGE = 2
EXIT_CANCELLED = 130


# options that only make sense with --papers-yml
_BATCH_ONLY = (
    "papers_dir",
    "summary",
    "check_references",
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group(
        "batch checking",
        "Check every paper listed by an aclpub2 papers.yml and write one summary row per paper. Paper types come from the input, so -p does not apply. -o/--output-dir and "
        "--temp-output-dir choose OUTPUT_DIR (default: aclpubcheck-batch).",
    )
    group.add_argument("--papers-yml", type=Path, metavar="PATH", help="aclpub2 papers.yml")
    group.add_argument(
        "--papers-dir",
        type=Path,
        metavar="DIR",
        help="directory holding the PDFs named in papers.yml (default: papers/ next to it)",
    )
    group.add_argument(
        "--summary",
        type=Path,
        metavar="PATH",
        help="summary file, tab-separated if it ends in .tsv (default: OUTPUT_DIR/summary.csv)",
    )
    group.add_argument(
        "--check-references",
        nargs="?",
        const="offline",
        choices=("offline", "online"),
        help="also run the bibliography checks, which only produce warnings; 'online' adds the "
        "check of author names in citations, which uploads each PDF to ref.scholarcy.com",
    )


def validate_mode(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Exit with a usage error unless the arguments ask for exactly one kind of run:
    PDF paths, or one batch source without the single-file options."""
    if is_batch(args):
        if args.submission_paths:
            parser.error("PDF paths cannot be combined with --papers-yml")
        if args.paper_type != parser.get_default("paper_type"):
            parser.error(
                "-p/--paper_type does not apply to batch checks: each paper's type comes from its input"
            )
        return
    if not args.submission_paths:
        parser.error("give PDF files or directories, or --papers-yml")
    given = [
        "--" + dest.replace("_", "-")
        for dest in _BATCH_ONLY
        if getattr(args, dest) != parser.get_default(dest)
    ]
    if given:
        parser.error(f"{', '.join(given)} only apply with --papers-yml")


def resolve_output_dir(args: argparse.Namespace) -> Path:
    """The batch OUTPUT_DIR: -o/--output-dir, a new temporary directory with
    --temp-output-dir, otherwise aclpubcheck-batch."""
    if args.temp_output_dir:
        return Path(tempfile.mkdtemp(prefix="aclpubcheck-"))
    return Path(args.output_dir) if args.output_dir else Path("aclpubcheck-batch")


def is_batch(args: argparse.Namespace) -> bool:
    return args.papers_yml is not None


def _load(args: argparse.Namespace, sink: EventSink) -> tuple[list[PaperRecord], PdfProvider]:
    sink(StageChanged("loading", str(args.papers_yml)))
    papers_dir = args.papers_dir or args.papers_yml.parent / "papers"
    return load_papers_yml(args.papers_yml), LocalPdfProvider(papers_dir)


def _unwritable(directory: Path) -> str:
    """Why files cannot be created in `directory`, checked without creating anything."""
    for existing in (directory, *directory.parents):
        if existing.exists():
            if not existing.is_dir():
                return f"{existing} is not a directory"
            if not os.access(existing, os.W_OK | os.X_OK):
                return f"{existing} is not writable"
            return ""
    return ""


def _write_snapshot(path: Path, args: argparse.Namespace, records: list[PaperRecord]) -> None:
    """The input exactly as loaded, to tell later runs and revisions apart."""
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "loaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {"papers_yml": str(args.papers_yml)},
        "records": [asdict(record) for record in records],
    }
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf8")


def batch_config(args: argparse.Namespace, config: CheckConfig) -> CheckConfig:
    """The name check uploads each PDF, so for a whole volume it runs only when asked for."""
    return replace(config, name_check=config.name_check and args.check_references == "online")


def make_job(args: argparse.Namespace, config: CheckConfig, summary: Path) -> Job:
    async def job(sink: EventSink, cancel: asyncio.Event) -> tuple[PaperResult, ...]:
        records, provider = _load(args, sink)
        _write_snapshot(args.output_dir / "input.json", args, records)
        if not records:
            write_summary(summary, ())
            sink(StageChanged("done", "no papers found in the input"))
            return ()
        options = RunOptions(
            report_root=args.output_dir / "reports",
            num_workers=args.num_workers,
            check=batch_config(args, config),
            check_references=args.check_references is not None,
        )
        return await run_batch(
            records, provider, options, fan_out(sink, SummaryWriter(summary)), cancel
        )

    return job


async def _run_plain(job: Job, sink: EventSink) -> tuple[PaperResult, ...]:
    cancel = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)

    def stop() -> None:
        if not cancel.is_set():
            print("cancelling: unfinished papers will be marked cancelled", file=sys.stderr)
            cancel.set()

    for number in signals:
        with suppress(NotImplementedError):  # no loop signal handlers on Windows
            loop.add_signal_handler(number, stop)
    try:
        return await job(sink, cancel)
    finally:
        for number in signals:
            with suppress(NotImplementedError):
                loop.remove_signal_handler(number)


def run(args: argparse.Namespace, config: CheckConfig) -> int:
    """Run a batch from parsed arguments; returns the process exit code."""
    if args.num_workers < 1:
        print("--num_workers must be at least 1", file=sys.stderr)
        return EXIT_USAGE
    args.output_dir = resolve_output_dir(args)
    summary = args.summary or args.output_dir / "summary.csv"
    for directory in (args.output_dir, summary.parent):
        problem = _unwritable(directory)
        if problem:
            print(f"cannot write to {directory}: {problem}", file=sys.stderr)
            return EXIT_USAGE
    print(f"Saving reports to {args.output_dir}", file=sys.stderr)
    job = make_job(args, config, summary)
    try:
        results = asyncio.run(_run_plain(job, ConsoleSink()))
    except ManifestError as error:
        print(error, file=sys.stderr)
        return EXIT_USAGE
    print(f"summary: {summary}", file=sys.stderr)
    if not results:
        return EXIT_NO_PAPERS
    if any(result.status is Status.CANCELLED for result in results):
        return EXIT_CANCELLED
    return 0
