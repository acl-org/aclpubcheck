"""One CSV/TSV row per expected paper, rewritten as the run progresses."""

from __future__ import annotations

import asyncio
import csv
import io
import math
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from .model import (
    Event,
    Finding,
    PaperChanged,
    PaperResult,
    RunFinished,
    RunStarted,
    printable,
    write_atomically,
)

COLUMNS = (
    "paper_id",
    "status",
    "paper_type",
    "file",
    "title",
    "authors",
    "emails",
    "error_count",
    "warning_count",
    "error_categories",
    "details",
    "report_dir",
    "pdf_sha256",
    "source",
    "source_id",
    "duration_s",
    "input_notes",
)

_FORMULA_PREFIXES = ("=", "+", "-", "@")
# submitter-controlled text; identifiers, hashes and paths are written verbatim
_FREE_TEXT = {"title", "authors", "emails", "details", "input_notes"}


def _categories(findings: Iterable[Finding]) -> str:
    return "; ".join(dict.fromkeys(f.category for f in findings))


def _cell(column: str, value: str) -> str:
    """One line of printable text; free text that a spreadsheet would evaluate as a formula,
    such as a title "=HYPERLINK(...)", gets a leading quote."""
    value = printable(value)
    if column in _FREE_TEXT and value.lstrip().startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def summary_row(result: PaperResult) -> dict[str, str]:
    """The summary columns for one paper, escaped for spreadsheet use."""
    record = result.record
    details = [f"{f.category}: {f.message}" for f in (*result.errors, *result.warnings)]
    if result.message:
        details.insert(0, result.message)
    row = {
        "paper_id": record.paper_id,
        "status": result.status.value,
        "paper_type": record.paper_type or "",
        # the PDF that was checked; the declared name when none was obtained
        "file": str(result.pdf.path) if result.pdf else record.file,
        "title": record.title,
        "authors": "; ".join(a.name for a in record.authors),
        "emails": "; ".join(a.email for a in record.authors if a.email),
        "error_count": str(len(result.errors)),
        "warning_count": str(len(result.warnings)),
        "error_categories": _categories(result.errors),
        "details": " | ".join(details),
        "report_dir": str(result.report_dir or ""),
        "pdf_sha256": result.pdf.sha256 if result.pdf else "",
        "source": record.source,
        "source_id": record.source_id,
        "duration_s": f"{result.duration:.2f}" if result.duration is not None else "",
        "input_notes": "; ".join(record.notes),
    }
    return {column: _cell(column, value) for column, value in row.items()}


def write_summary(path: Path, results: Iterable[PaperResult]) -> None:
    """Atomically replace `path`; a .tsv suffix selects tab separation."""
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=COLUMNS, delimiter=delimiter)
    writer.writeheader()
    writer.writerows(summary_row(result) for result in results)
    write_atomically(path, text.getvalue().encode("utf8"))


class SummaryWriter:
    """Event sink keeping `path` current with every paper's latest state.

    The file is rewritten at most once per `interval` seconds while papers change (a later
    write is scheduled on the event loop, so no change waits for the next event), and at once
    when the run starts and finishes. A killed run therefore still shows, at most `interval`
    late, which papers had not finished; rewriting all rows on every
    change instead would cost O(papers^2) and stall the loop when a big run is cancelled."""

    def __init__(
        self, path: Path, interval: float = 1.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.path = path
        self._interval = interval
        self._clock = clock
        self._results: dict[int, PaperResult] = {}
        self._written = -math.inf
        self._pending: asyncio.TimerHandle | None = None

    def __call__(self, event: Event) -> None:
        if isinstance(event, (RunStarted, RunFinished)):
            self._results = {result.record.index: result for result in event.results}
            self.flush()
        elif isinstance(event, PaperChanged):
            self._results[event.result.record.index] = event.result
            if self._pending is None:
                delay = self._interval - (self._clock() - self._written)
                if delay <= 0:
                    self.flush()
                else:
                    self._pending = asyncio.get_running_loop().call_later(delay, self.flush)

    def flush(self) -> None:
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None
        write_summary(self.path, self._results.values())
        self._written = self._clock()
