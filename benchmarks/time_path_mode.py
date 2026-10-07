"""Time path-mode runs over the papers of a papers.yml with several installed aclpubcheck versions.

    python benchmarks/time_path_mode.py PATH/papers.yml --workers 1,4 \
        main=PATH/TO/main-venv/bin/python "this version=.venv/bin/python"

Each version checks the papers as a chair would without batch mode: one
`python -m aclpubcheck -p TYPE FILES...` run per paper type. The script prints a Markdown
table of wall times, and whether every version wrote the same reports.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from aclpubcheck.batch.manifest import load_papers_yml


def papers_by_type(papers_yml: Path, papers_dir: Path) -> dict[str, list[str]]:
    """Absolute PDF paths of every checkable paper, by paper type."""
    groups: dict[str, list[str]] = {}
    for record in load_papers_yml(papers_yml):
        path = papers_dir / record.file
        if record.paper_type and not record.problems and path.is_file():
            groups.setdefault(record.paper_type, []).append(str(path.resolve()))
    return groups


def time_version(python: str, groups: dict[str, list[str]], out: Path, workers: int) -> float:
    # without PYTHONPATH, and outside any checkout, so each python imports its own aclpubcheck
    env = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
    out.mkdir(parents=True)
    started = time.perf_counter()
    for paper_type, files in groups.items():
        command = [python, "-m", "aclpubcheck", "-p", paper_type, "--num_workers", str(workers)]
        done = subprocess.run(
            [*command, "-o", str(out / paper_type), *files],
            cwd=out,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if done.returncode != 0:
            raise SystemExit(f"{python} -m aclpubcheck failed:\n{done.stderr[-4000:]}")
    seconds = time.perf_counter() - started
    papers = sum(len(files) for files in groups.values())
    written = len(list(out.rglob("errors-*.json")))
    if written != papers:  # path mode skips what it does not take for a PDF, and exits 0
        raise SystemExit(f"{python} wrote {written} reports for {papers} papers")
    return seconds


def reports(directory: Path) -> dict[str, object]:
    return {
        str(path.relative_to(directory)): json.loads(path.read_text(encoding="utf8"))
        for path in sorted(directory.rglob("errors-*.json"))
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("papers_yml", type=Path)
    parser.add_argument("versions", nargs="+", metavar="LABEL=PYTHON")
    parser.add_argument("--papers-dir", type=Path, help="default: papers/ next to papers.yml")
    parser.add_argument("--workers", default="1,4", help="comma-separated (default: 1,4)")
    args = parser.parse_args()
    versions = [version.split("=", 1) for version in args.versions]
    if any(len(version) != 2 for version in versions):
        parser.error("give each version as LABEL=PYTHON")
    groups = papers_by_type(args.papers_yml, args.papers_dir or args.papers_yml.parent / "papers")
    workers = [int(count) for count in args.workers.split(",")]

    rows = []
    identical = True
    with tempfile.TemporaryDirectory(prefix="aclpubcheck-timing-") as directory:
        for count in workers:
            seconds = []
            written = []
            for index, (label, python) in enumerate(versions):
                out = Path(directory) / f"{count}-{index}"
                seconds.append(time_version(python, groups, out, count))
                written.append(reports(out))
                print(f"{label}, {count} worker(s): {seconds[-1]:.1f} s", file=sys.stderr)
            identical = identical and all(other == written[0] for other in written[1:])
            rows.append(f"| {count} | " + " | ".join(f"{s:.1f} s" for s in seconds) + " |")

    papers = sum(len(files) for files in groups.values())
    print(f"{papers} papers of {args.papers_yml}, one path-mode run per paper type.\n")
    print("| workers | " + " | ".join(label for label, _ in versions) + " |")
    print("| ---: |" + " ---: |" * len(versions))
    print("\n".join(rows))
    print(f"\nReports identical across versions: {'yes' if identical else 'no'}")


if __name__ == "__main__":
    main()
