"""Line-based progress for terminals, pipes and log files."""

from __future__ import annotations

import sys
import time
from collections import Counter
from typing import TextIO

from .model import (
    Event,
    PaperChanged,
    PaperResult,
    RunFinished,
    RunStarted,
    StageChanged,
    Status,
    printable,
)


def status_counts(results: tuple[PaperResult, ...]) -> str:
    counts = Counter(result.status for result in results)
    return ", ".join(f"{counts[status]} {status.value}" for status in Status if counts[status])


class ConsoleSink:
    """Prints one line per stage and per finished paper, with progress and a rough ETA."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stderr
        self._total = 0
        self._done = 0
        self._started = time.monotonic()

    def _print(self, line: str) -> None:
        print(printable(line), file=self._stream, flush=True)

    def __call__(self, event: Event) -> None:
        if isinstance(event, StageChanged):
            self._print(f"== {event.stage}" + (f": {event.detail}" if event.detail else ""))
        elif isinstance(event, RunStarted):
            self._total, self._done = len(event.results), 0
            self._started = time.monotonic()
            self._print(f"== checking {self._total} papers")
        elif isinstance(event, PaperChanged) and event.result.status.terminal:
            self._done += 1
            # papers cancelled together are counted in the final line instead
            if event.result.status is not Status.CANCELLED:
                self._print(self._line(event.result))
        elif isinstance(event, RunFinished):
            elapsed = time.monotonic() - self._started
            verb = "cancelled after" if event.cancelled else "finished in"
            self._print(f"== {verb} {elapsed:.1f}s: {status_counts(event.results)}")

    def _line(self, result: PaperResult) -> str:
        elapsed = time.monotonic() - self._started
        eta = elapsed / self._done * (self._total - self._done)
        width = len(str(self._total))
        duration = f"{result.duration:.1f}s" if result.duration is not None else "-"
        problems = "; ".join(dict.fromkeys(f.category for f in result.errors)) or result.message
        return (
            f"[{self._done:>{width}}/{self._total}] {result.record.paper_id} {result.status.value} "
            f"({duration}, eta {eta:.0f}s)" + (f" {problems}" if problems else "")
        )
