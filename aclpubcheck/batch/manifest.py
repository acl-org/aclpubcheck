"""aclpub2 papers.yml input: one record per entry, with PDFs read from a local directory."""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path, PurePath

import yaml

from .model import (
    Author,
    FetchedPdf,
    FetchError,
    PaperRecord,
    Status,
    metadata_notes,
    normalize_paper_type,
    paper_type_problem,
    sha256_file,
    text,
)

_EMAIL = re.compile(r"^[^@\s\x00-\x1f\x7f]+@[^@\s\x00-\x1f\x7f]+\.[^@\s\x00-\x1f\x7f]+$")


class ManifestError(ValueError):
    """The manifest as a whole is unusable, so no record can be trusted."""


def _emails(value: object) -> list[str]:
    # aclpub2 escapes underscores for LaTeX
    values = value if isinstance(value, list) else [value]
    return [text(v).replace("\\_", "_") for v in values if text(v)]


def _author(entry: object, number: int, notes: list[str]) -> Author:
    if not isinstance(entry, Mapping):
        notes.append(f"author {number} is not a mapping")
        return Author(name="")
    name = text(entry.get("name")) or " ".join(
        part
        for part in (text(entry.get(key)) for key in ("first_name", "middle_name", "last_name"))
        if part
    )
    if not name:
        notes.append(f"author {number} has no name")
    emails = _emails(entry.get("email") or entry.get("emails"))
    for email in emails:
        if not _EMAIL.match(email):
            notes.append(f"author {name or number} has an invalid email {email!r}")
    return Author(
        name=name,
        email=", ".join(emails),
    )


def _record(index: int, entry: object, source: str) -> PaperRecord:
    if not isinstance(entry, Mapping):
        return PaperRecord(
            index=index,
            paper_id=f"#{index + 1}",
            source=source,
            problems=("entry is not a mapping",),
        )
    problems: list[str] = []
    notes: list[str] = []
    paper_id = text(entry.get("id"))
    if not paper_id:
        problems.append("missing id")
    file = text(entry.get("file"))
    if not file:
        problems.append("missing file")
    elif PurePath(file).is_absolute() or ".." in PurePath(file).parts:
        problems.append(f"file {file!r} must be a relative path inside the papers directory")
    attributes = entry.get("attributes")
    raw_type = attributes.get("paper_type") if isinstance(attributes, Mapping) else None
    if problem := paper_type_problem(raw_type, "attributes.paper_type"):
        problems.append(problem)
    title = text(entry.get("title"))
    raw_authors = entry.get("authors")
    listed = raw_authors if isinstance(raw_authors, list) else []
    authors = tuple(_author(author, number, notes) for number, author in enumerate(listed, 1))
    notes += metadata_notes(title, authors)
    return PaperRecord(
        index=index,
        paper_id=paper_id or f"#{index + 1}",
        title=title,
        paper_type=normalize_paper_type(raw_type),
        authors=authors,
        file=file,
        source=source,
        source_id=paper_id,
        problems=tuple(problems),
        notes=tuple(notes),
    )


def _flag_shared(records: list[PaperRecord]) -> list[PaperRecord]:
    """Repeated ids make reports unattributable, so those entries are not checked; a shared file is only noted."""
    ids = Counter(record.source_id for record in records if record.source_id)
    files = Counter(record.file for record in records if record.file)
    flagged = []
    for record in records:
        problems, notes = record.problems, record.notes
        if ids[record.source_id] > 1:
            problems += (f"id {record.source_id} is used by {ids[record.source_id]} entries",)
        if files[record.file] > 1:
            notes += (f"file {record.file} is used by {files[record.file]} entries",)
        flagged.append(replace(record, problems=problems, notes=notes))
    return flagged


def load_papers_yml(path: Path) -> list[PaperRecord]:
    """Records for every entry of an aclpub2 papers.yml, in file order; bad entries carry problems."""
    try:
        entries = yaml.safe_load(path.read_text(encoding="utf8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise ManifestError(f"{path}: cannot read papers: {error}") from error
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ManifestError(f"{path}: expected a list of papers, got {type(entries).__name__}")
    source = str(path)
    return _flag_shared([_record(index, entry, source) for index, entry in enumerate(entries)])


class LocalPdfProvider:
    """PDFs named by each record's `file`, under one directory."""

    def __init__(self, papers_dir: Path) -> None:
        self.papers_dir = papers_dir

    async def fetch(self, record: PaperRecord) -> FetchedPdf:
        path = self.papers_dir / record.file
        if not path.is_file():
            raise FetchError(Status.MISSING_FILE, f"{path} not found")
        try:
            sha256 = await asyncio.to_thread(sha256_file, path)
        except OSError as error:
            raise FetchError(
                Status.MISSING_FILE, f"{path} cannot be read: {error.strerror or error}"
            ) from error
        return FetchedPdf(path=path, sha256=sha256)
