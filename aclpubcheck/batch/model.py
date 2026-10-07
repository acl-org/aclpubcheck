"""Records, statuses and events shared by the batch modules."""

from __future__ import annotations

import asyncio
import enum
import hashlib
import os
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


def write_atomically(path: Path, data: bytes) -> None:
    """Replace `path` with `data` in one rename.

    The temporary file is created normally, so the result gets the usual umask permissions
    rather than tempfile.mkstemp's 0600, which would hide a shared summary from co-chairs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("xb") as stream:
            stream.write(data)
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def describe_error(error: BaseException) -> str:
    """How a failure appears in the summary: the exception type and its message."""
    return f"{type(error).__name__}: {error}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Status(str, enum.Enum):
    """Where a paper is in the run; every expected paper ends in exactly one terminal status."""

    QUEUED = "queued"
    CHECKING = "checking"
    PASSED = "passed"
    WARNINGS = "warnings"
    VIOLATIONS = "violations"
    MISSING_FILE = "missing_file"
    CHECK_ERROR = "check_error"
    INVALID_INPUT = "invalid_input"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self not in (Status.QUEUED, Status.CHECKING)

    @property
    def problem(self) -> bool:
        """The paper needs attention: it failed, or it was never fully checked."""
        return self.terminal and self not in (Status.PASSED, Status.WARNINGS)


PAPER_TYPES = ("long", "short", "demo", "other")


def safe_id(paper_id: str) -> str:
    """`paper_id` as a file name part: other characters become "_", cut to 60 characters."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", paper_id)[:60] or "paper"


def version_name(paper_id: str, sha256: str, index: int | None = None) -> str:
    """A file-system-safe name for one version of a paper's PDF: "<id>[-<index>]-<sha256[:12]>".

    The index tells apart ids that become the same safe_id; the hash suffix keeps names
    like ".." from meaning anything to the file system.
    """
    middle = "" if index is None else f"-{index}"
    return f"{safe_id(paper_id)}{middle}-{sha256[:12]}"


# C0/C1 controls, Unicode line and paragraph separators, and bidirectional overrides
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")
_SPACES = "\t\n\r\u2028\u2029"


def printable(text: str) -> str:
    """`text` safe to print or put in one CSV/TSV cell: line breaks and tabs become spaces,
    other control characters (NUL, ESC, bidi overrides, ...) become U+FFFD, so author-supplied
    titles cannot send terminal escape sequences, break a row or reorder what is shown."""
    return _CONTROL.sub(lambda m: " " if m.group() in _SPACES else "\ufffd", text)


def normalize_paper_type(raw: object) -> str | None:
    """Map a declared type ("long", "Long Paper", "short paper") to a page-limit type, or None."""
    words = str(raw).strip().lower().split() if raw is not None else []
    return words[0] if words and words[0] in PAPER_TYPES else None


def text(value: object) -> str:
    """A declared value as stripped text; a missing value is empty."""
    return "" if value is None else str(value).strip()


def paper_type_problem(raw: object, field: str) -> str:
    """Why the declared paper type cannot be used, or "" when it can. Never defaulted."""
    if raw is None:
        return f"missing {field}"
    if normalize_paper_type(raw) is None:
        return f"unknown paper_type {raw!r} (expected one of {', '.join(PAPER_TYPES)})"
    return ""


@dataclass(frozen=True)
class Author:
    name: str
    email: str = ""


def metadata_notes(title: str, authors: Sequence[Author]) -> list[str]:
    """Input warnings every source shares; the paper is still checked."""
    notes = [] if title else ["missing title"]
    if not authors:
        notes.append("no authors listed")
    elif not any(author.email for author in authors):
        notes.append("no author email")
    return notes


@dataclass(frozen=True)
class PaperRecord:
    """One expected paper, as declared by the input (a papers.yml entry)."""

    index: int  # position in the input; the key that stays unique when ids repeat
    paper_id: str
    title: str = ""
    paper_type: str | None = None  # normalized; None when the declared type is unusable
    authors: tuple[Author, ...] = ()
    file: str = ""  # PDF file name declared by the input
    source: str = ""  # the papers.yml path that was read
    source_id: str = ""  # the id the input gives the paper
    problems: tuple[str, ...] = ()  # input errors: the paper cannot be checked
    notes: tuple[str, ...] = ()  # input warnings: the paper is still checked


@dataclass(frozen=True)
class FetchedPdf:
    """The exact PDF bytes a check ran on."""

    path: Path
    sha256: str


class FetchError(Exception):
    """The PDF for a paper could not be obtained; `status` says how the paper ends."""

    def __init__(self, status: Status, message: str) -> None:
        super().__init__(message)
        self.status = status


class PdfProvider(Protocol):
    """Resolves a record to a local PDF; raises FetchError when it cannot."""

    async def fetch(self, record: PaperRecord) -> FetchedPdf: ...


@dataclass(frozen=True)
class Finding:
    category: str  # Error/Warn value, e.g. "Margin" or "Bibliography"
    message: str


@dataclass(frozen=True)
class CheckOutcome:
    """What one format check produced; sent back from the worker process."""

    status: Status
    errors: tuple[Finding, ...] = ()
    warnings: tuple[Finding, ...] = ()
    message: str = ""  # why the check is incomplete, for check_error
    report_files: tuple[str, ...] = ()
    duration: float = 0.0


@dataclass(frozen=True)
class PaperResult:
    record: PaperRecord
    status: Status = Status.QUEUED
    pdf: FetchedPdf | None = None
    errors: tuple[Finding, ...] = ()
    warnings: tuple[Finding, ...] = ()
    message: str = ""
    report_dir: Path | None = None
    report_files: tuple[str, ...] = ()
    duration: float | None = None  # seconds spent fetching and checking, without queueing


@dataclass(frozen=True)
class StageChanged:
    stage: str
    detail: str = ""


@dataclass(frozen=True)
class RunStarted:
    results: tuple[PaperResult, ...]


@dataclass(frozen=True)
class PaperChanged:
    result: PaperResult


@dataclass(frozen=True)
class RunFinished:
    results: tuple[PaperResult, ...]
    cancelled: bool


Event = StageChanged | RunStarted | PaperChanged | RunFinished
EventSink = Callable[[Event], None]
# a whole batch run (loading included), driven by the console
Job = Callable[[EventSink, asyncio.Event], Awaitable[tuple[PaperResult, ...]]]


def fan_out(*sinks: EventSink) -> EventSink:
    def publish(event: Event) -> None:
        for sink in sinks:
            sink(event)

    return publish
