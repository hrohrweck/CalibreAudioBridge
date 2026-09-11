"""Ledger: run records and per-(book, variant) job state."""

from __future__ import annotations

from pathlib import Path

from calibreaudiobridge.enums import JobStatus, Variant
from calibreaudiobridge.state import Job, Ledger, RunOutcome


class TestLedger_runs:
    def test_run_roundtrip(self, tmp_path: Path) -> None:
        with Ledger(tmp_path / "ledger.db") as ledger:
            run_id = ledger.start_run()
            ledger.finish_run(
                run_id,
                RunOutcome(
                    exit_code=0, books_done=2, books_failed=1, tokens_in=100, tokens_out=50
                ),
            )
            (run,) = ledger.last_runs(limit=1)
        assert run.id == run_id
        assert run.exit_code == 0
        assert run.books_done == 2
        assert run.books_failed == 1
        assert run.finished is not None


class TestLedger_jobs:
    def test_job_upsert_roundtrip(self, tmp_path: Path) -> None:
        with Ledger(tmp_path / "ledger.db") as ledger:
            ledger.put_job(
                Job(
                    book_id=7,
                    variant=Variant.AUDIOBOOK,
                    status=JobStatus.PROCESSING,
                    attempts=1,
                    last_chunk=12,
                )
            )
            ledger.put_job(
                Job(
                    book_id=7,
                    variant=Variant.AUDIOBOOK,
                    status=JobStatus.DONE,
                    attempts=2,
                    last_chunk=12,
                    finished="2026-09-11T10:00:00+00:00",
                )
            )
            job = ledger.get_job(7, Variant.AUDIOBOOK)
        assert job is not None
        assert job.status is JobStatus.DONE
        assert job.attempts == 2

    def test_get_missing_job_returns_none(self, tmp_path: Path) -> None:
        with Ledger(tmp_path / "ledger.db") as ledger:
            assert ledger.get_job(99, Variant.SUMMARY) is None

    def test_jobs_filters_by_status(self, tmp_path: Path) -> None:
        with Ledger(tmp_path / "ledger.db") as ledger:
            ledger.put_job(Job(book_id=1, variant=Variant.AUDIOBOOK, status=JobStatus.DONE))
            ledger.put_job(Job(book_id=2, variant=Variant.SUMMARY, status=JobStatus.FAILED))
            done = ledger.jobs([JobStatus.DONE])
            assert [j.book_id for j in done] == [1]
