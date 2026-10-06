"""An offline papers.yml sample covering every way a paper can end, and shared test helpers."""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from pathlib import Path

import yaml
from pdf_fixtures import MARGIN_TEXT, REFERENCES, TEXT, write_pages, write_pdf

from aclpubcheck.batch.check import CheckJob
from aclpubcheck.batch.model import CheckOutcome, Event, PaperResult


def entry(
    paper_id: int,
    file: str,
    paper_type: str = "long",
    title: str | None = None,
    authors: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "id": paper_id,
        "file": file,
        "title": title or f"Paper {paper_id}",
        "attributes": {"paper_type": paper_type},
        "authors": authors
        if authors is not None
        else [
            {
                "first_name": "Ada",
                "last_name": f"Author{paper_id}",
                "email": f"author\\_{paper_id}@example.org",
            }
        ],
    }


# paper id -> expected terminal status
EXPECTED = {
    "1": "passed",
    "2": "violations",  # text in the right margin
    "3": "violations",  # references start on page 11 of a long paper
    "4": "check_error",  # not a PDF
    "5": "check_error",  # no text, so the font check cannot run
    "6": "missing_file",
    "7": "invalid_input",  # unusable paper type
    "8": "invalid_input",  # id 8 appears twice
    "12": "passed",  # 12_camera.pdf and 12_old.pdf share the errors-12 report prefix
    "13": "violations",
}


def write_sample(root: str | Path) -> Path:
    """Write papers/ and papers.yml under root; return the papers.yml path."""
    papers = Path(root) / "papers"
    papers.mkdir(parents=True)
    write_pdf(papers / "1.pdf", TEXT)
    write_pdf(papers / "2.pdf", MARGIN_TEXT)
    write_pages(papers / "3.pdf", [TEXT] * 10 + [REFERENCES])
    (papers / "4.pdf").write_bytes(b"this is not a pdf")
    write_pdf(papers / "5.pdf", b"")
    write_pdf(papers / "7.pdf", TEXT)
    write_pdf(papers / "8.pdf", TEXT)
    write_pdf(papers / "12_camera.pdf", TEXT)
    write_pdf(papers / "12_old.pdf", MARGIN_TEXT)
    entries = [
        entry(1, "1.pdf"),
        entry(2, "2.pdf", paper_type="Short Paper"),
        entry(3, "3.pdf"),
        entry(4, "4.pdf"),
        entry(5, "5.pdf"),
        entry(6, "6.pdf"),
        entry(7, "7.pdf", paper_type="N/A"),
        entry(8, "8.pdf"),
        entry(8, "8.pdf", title="Same id again"),
        entry(12, "12_camera.pdf", authors=[]),
        entry(13, "12_old.pdf"),
    ]
    manifest = Path(root) / "papers.yml"
    manifest.write_text(yaml.safe_dump(entries, allow_unicode=True), encoding="utf8")
    return manifest


def crash_on_marker(job: CheckJob) -> CheckOutcome:
    """A run_check that kills its worker process for crash*.pdf.

    It runs in spawned workers, which import this module through the inherited sys.path.
    The crash comes while the other checks are still in flight, so the pool breaks under them.
    """
    from aclpubcheck.batch.check import run_check

    if job.pdf_path.name.startswith("crash"):
        time.sleep(0.3)
        os._exit(1)
    time.sleep(1.0)
    return run_check(job)


class Recorder:
    """An event sink that keeps every event."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def __call__(self, event: Event) -> None:
        self.events.append(event)


def semantic(results: Sequence[PaperResult]) -> list[tuple[object, ...]]:
    """What a run decided for each paper, without timings or paths."""
    return [(r.record.index, r.status, r.errors, r.warnings, r.message) for r in results]
