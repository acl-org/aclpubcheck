"""Async scheduling: fetch each paper's PDF, check it in a process pool, and publish every state change."""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import time
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..formatchecker import CheckConfig
from .check import CheckJob, ignore_sigint, run_check
from .model import (
    CheckOutcome,
    EventSink,
    FetchedPdf,
    FetchError,
    PaperChanged,
    PaperRecord,
    PaperResult,
    PdfProvider,
    RunFinished,
    RunStarted,
    Status,
    describe_error,
    safe_id,
    version_name,
)

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunOptions:
    report_root: Path
    num_workers: int = 1
    check: CheckConfig = field(default_factory=CheckConfig)
    check_references: bool = False


class _Table:
    """The latest result for every record; each change is published to the sink."""

    def __init__(self, records: Sequence[PaperRecord], sink: EventSink) -> None:
        self._results = {record.index: PaperResult(record) for record in records}
        self._sink = sink

    def update(self, index: int, **changes: Any) -> None:
        result = replace(self._results[index], **changes)
        self._results[index] = result
        self._sink(PaperChanged(result))

    def snapshot(self) -> tuple[PaperResult, ...]:
        return tuple(self._results.values())


def _spawn_pool(workers: int) -> ProcessPoolExecutor:
    # spawn: blocking work runs in threads, and forking a threaded process can deadlock
    return ProcessPoolExecutor(
        max_workers=workers,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=ignore_sigint,
    )


class _CheckPool:
    """Format checks in worker processes.

    When a worker dies, every check in flight fails with BrokenProcessPool, not only the one
    that crashed. Each of them is retried alone in a one-process retry lane, so a paper ends
    as check_error only when its own check crashes; serial and parallel runs then agree.
    """

    def __init__(self, workers: int) -> None:
        self._workers = workers
        self._main = _spawn_pool(workers)
        self._lane: ProcessPoolExecutor | None = None
        self._lane_turn = asyncio.Lock()

    async def run(self, job: CheckJob) -> CheckOutcome:
        loop = asyncio.get_running_loop()
        executor = self._main
        try:
            return await loop.run_in_executor(executor, run_check, job)
        except BrokenProcessPool:
            if self._main is executor:
                executor.shutdown(wait=False)
                self._main = _spawn_pool(self._workers)
        async with self._lane_turn:
            if self._lane is None:
                self._lane = _spawn_pool(1)
            try:
                return await loop.run_in_executor(self._lane, run_check, job)
            except BrokenProcessPool:
                self._lane.shutdown(wait=False)
                self._lane = None
        return CheckOutcome(
            status=Status.CHECK_ERROR,
            message="the check process crashed on this PDF, also when it was retried alone",
        )

    def close(self, terminate: bool) -> None:
        for executor in (self._main, self._lane):
            if executor is None:
                continue
            if terminate:
                _terminate_workers(executor)
            executor.shutdown(wait=not terminate, cancel_futures=True)


def _terminate_workers(executor: ProcessPoolExecutor) -> None:
    stop = getattr(executor, "terminate_workers", None)  # Python 3.14+
    if stop is not None:
        stop()
        return
    # older Pythons have no public way to stop a running check
    for process in list((getattr(executor, "_processes", None) or {}).values()):
        process.terminate()


async def run_batch(
    records: Sequence[PaperRecord],
    provider: PdfProvider,
    options: RunOptions,
    sink: EventSink,
    cancel: asyncio.Event | None = None,
) -> tuple[PaperResult, ...]:
    """Check every record and return one terminal result per record, in input order.

    At most `num_workers` checks run at once, and a PDF is read only once its check has a
    slot. A paper whose PDF cannot be read or checked ends with its own status and the run
    goes on.

    Setting `cancel`, or cancelling the calling task, stops the run: unfinished papers
    end as cancelled, and RunFinished is published before returning (or re-raising).
    """
    if len({record.index for record in records}) != len(records):
        raise ValueError("record indexes must be unique")
    table = _Table(records, sink)
    sink(RunStarted(table.snapshot()))
    pool = _CheckPool(max(1, options.num_workers))
    check_slots = asyncio.Semaphore(max(1, options.num_workers))
    # ids that become the same directory name get the record index in it, decided up front
    safe_id_counts = Counter(safe_id(r.paper_id) for r in records if not r.problems)

    async def fetch(record: PaperRecord) -> tuple[FetchedPdf, float] | None:
        started = time.perf_counter()
        try:
            return await provider.fetch(record), time.perf_counter() - started
        except FetchError as error:
            status, message = error.status, str(error)
        except Exception as error:  # noqa: BLE001 -- one paper's failure must not end the run
            _log.exception("could not get the PDF of paper %s", record.paper_id)
            status = Status.MISSING_FILE
            message = describe_error(error)
        table.update(
            record.index, status=status, message=message, duration=time.perf_counter() - started
        )
        return None

    async def check(record: PaperRecord, pdf: FetchedPdf, fetch_time: float) -> None:
        assert record.paper_type is not None  # a record without a usable type has problems
        index = record.index if safe_id_counts[safe_id(record.paper_id)] > 1 else None
        # one directory per paper and PDF version, so revisions never overwrite each other
        report_dir = options.report_root / version_name(record.paper_id, pdf.sha256, index)
        table.update(record.index, status=Status.CHECKING, pdf=pdf, report_dir=report_dir)
        job = CheckJob(
            pdf.path,
            record.paper_type,
            report_dir,
            options.check,
            options.check_references,
            pdf.sha256,
        )
        try:
            outcome = await pool.run(job)
        except Exception as error:  # noqa: BLE001 -- one paper's failure must not end the run
            _log.exception("could not check paper %s", record.paper_id)
            outcome = CheckOutcome(status=Status.CHECK_ERROR, message=describe_error(error))
        table.update(
            record.index,
            status=outcome.status,
            errors=outcome.errors,
            warnings=outcome.warnings,
            message=outcome.message,
            report_files=outcome.report_files,
            duration=fetch_time + outcome.duration,
        )

    async def process(record: PaperRecord) -> None:
        if record.problems:
            table.update(
                record.index, status=Status.INVALID_INPUT, message="; ".join(record.problems)
            )
            return
        async with check_slots:
            fetched = await fetch(record)
            if fetched is not None:
                await check(record, *fetched)

    tasks = [asyncio.ensure_future(process(record)) for record in records]
    cancelled = False
    try:
        work = asyncio.gather(*tasks)
        # after a cancel nobody awaits the gather; reading its outcome keeps asyncio from
        # logging "exception was never retrieved"
        work.add_done_callback(lambda done: done.cancelled() or done.exception())
        if cancel is None:
            await work
        else:
            stop = asyncio.ensure_future(cancel.wait())
            await asyncio.wait({work, stop}, return_when=asyncio.FIRST_COMPLETED)
            stop.cancel()
            cancelled = not work.done()
            if not cancelled:
                work.result()
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        unfinished = [task for task in tasks if not task.done()]
        for task in unfinished:
            task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.gather(*unfinished, return_exceptions=True)
        pool.close(terminate=cancelled or bool(unfinished))
        for result in table.snapshot():
            if not result.status.terminal:
                table.update(result.record.index, status=Status.CANCELLED)
        sink(RunFinished(table.snapshot(), cancelled))
    return table.snapshot()
