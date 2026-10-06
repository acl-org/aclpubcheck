"""The format check for one PDF, run inside a worker process."""

from __future__ import annotations

import signal
import time
import traceback
from collections.abc import Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path

from ..formatchecker import CheckConfig, Error, Formatter, Warn
from ..name_check import PDFNameCheck
from .model import CheckOutcome, Finding, Status, describe_error, sha256_file

LOG_NAME = "check.log"


@dataclass(frozen=True)
class CheckJob:
    pdf_path: Path
    paper_type: str
    report_dir: Path
    config: CheckConfig = field(default_factory=CheckConfig)
    check_references: bool = False
    sha256: str = ""  # the hash recorded for the PDF; the check refuses other bytes


def classify(logs: Mapping[Error | Warn, Sequence[str]]) -> CheckOutcome:
    """The outcome a Formatter's logs describe.

    Parsing errors mean some pages were skipped, so they outrank format errors.
    """
    errors = tuple(
        Finding(kind.value, m)
        for kind, ms in logs.items()
        if isinstance(kind, Error) and kind is not Error.PARSING
        for m in ms
    )
    warnings = tuple(
        Finding(kind.value, m) for kind, ms in logs.items() if isinstance(kind, Warn) for m in ms
    )
    parsing = logs.get(Error.PARSING, ())
    if parsing:
        status = Status.CHECK_ERROR
    elif errors:
        status = Status.VIOLATIONS
    elif warnings:
        status = Status.WARNINGS
    else:
        status = Status.PASSED
    return CheckOutcome(status=status, errors=errors, warnings=warnings, message="; ".join(parsing))


class PdfChanged(Exception):
    """The file no longer holds the bytes whose hash names the report directory."""


@cache
def _name_check() -> PDFNameCheck:
    """Built once per worker process: the rebiber database takes seconds and ~1 GB to load."""
    return PDFNameCheck()


def _empty_report_dir(report_dir: Path) -> None:
    """Create `report_dir`, or empty it of an earlier check of the same PDF version.

    Reports are flat files, so nothing is removed recursively."""
    report_dir.mkdir(parents=True, exist_ok=True)
    for path in report_dir.iterdir():
        if path.is_file() or path.is_symlink():
            path.unlink()


def run_check(job: CheckJob) -> CheckOutcome:
    """Check one PDF, writing its JSON/PNG reports and console output into job.report_dir.

    A failure inside the checker, or in writing its reports, becomes a check_error outcome
    for this paper only.
    """
    started = time.perf_counter()
    try:
        _empty_report_dir(job.report_dir)
        log = (job.report_dir / LOG_NAME).open("w", encoding="utf8")
    except OSError as error:
        return CheckOutcome(
            status=Status.CHECK_ERROR,
            message=f"cannot write reports to {job.report_dir}: {error}",
            duration=time.perf_counter() - started,
        )
    formatter = Formatter(job.config)
    if job.check_references and job.config.name_check:
        formatter.pdf_namecheck = _name_check()
    with log, redirect_stdout(log), redirect_stderr(log):
        try:
            if job.sha256 and sha256_file(job.pdf_path) != job.sha256:
                raise PdfChanged(f"{job.pdf_path} changed after it was hashed; check it again")
            formatter.format_check(
                submission=str(job.pdf_path),
                paper_type=job.paper_type,
                output_dir=str(job.report_dir),
                check_references=job.check_references,
            )
        except Exception as error:  # noqa: BLE001 -- one unreadable PDF must not end the batch
            traceback.print_exc()
            # keep whatever the checks found before the failure
            outcome = replace(
                classify(formatter.logs), status=Status.CHECK_ERROR, message=describe_error(error)
            )
        else:
            outcome = classify(formatter.logs)
    return replace(
        outcome,
        report_files=tuple(sorted(p.name for p in job.report_dir.iterdir())),
        duration=time.perf_counter() - started,
    )


def ignore_sigint() -> None:
    """Pool initializer: Ctrl-C cancels the run in the parent, which then stops the workers."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
