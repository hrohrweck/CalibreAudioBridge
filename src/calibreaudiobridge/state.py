"""SQLite ledger: run history, per-book job state, TTS cache index (PLAN.md 9.1)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self

from .enums import JobStatus, Variant

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

_SCHEMA: str = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started TEXT NOT NULL,
    finished TEXT,
    exit_code INTEGER,
    books_done INTEGER NOT NULL DEFAULT 0,
    books_failed INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS jobs (
    book_id INTEGER NOT NULL,
    variant TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    source_hash TEXT NOT NULL DEFAULT '',
    config_hash TEXT NOT NULL DEFAULT '',
    last_chunk INTEGER NOT NULL DEFAULT 0,
    started TEXT,
    finished TEXT,
    error TEXT,
    PRIMARY KEY (book_id, variant)
);
CREATE TABLE IF NOT EXISTS tts_cache (
    hash TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    created TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class Job:
    """Per (book, variant) processing state; mirrors #cab_* columns."""

    book_id: int
    variant: Variant
    status: JobStatus
    attempts: int = 0
    source_hash: str = ""
    config_hash: str = ""
    last_chunk: int = 0
    started: str | None = None
    finished: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Aggregate result of one run."""

    exit_code: int
    books_done: int = 0
    books_failed: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass(frozen=True, slots=True)
class RunRecord:
    """One `cab run` invocation."""

    id: int
    started: str
    finished: str | None
    exit_code: int | None
    books_done: int
    books_failed: int
    tokens_in: int
    tokens_out: int


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        book_id=row["book_id"],
        variant=Variant(row["variant"]),
        status=JobStatus(row["status"]),
        attempts=row["attempts"],
        source_hash=row["source_hash"],
        config_hash=row["config_hash"],
        last_chunk=row["last_chunk"],
        started=row["started"],
        finished=row["finished"],
        error=row["error"],
    )


class Ledger:
    """Access layer for the job ledger. Use as a context manager."""

    def __init__(self, db_path: Path) -> None:
        """Open (and migrate) the ledger database."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path)
        self._db.row_factory = sqlite3.Row
        _ = self._db.execute("PRAGMA journal_mode=WAL")
        _ = self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        """Close the underlying connection."""
        self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def start_run(self) -> int:
        """Record a run start; return its id."""
        cur = self._db.execute("INSERT INTO runs (started) VALUES (?)", (_now(),))
        self._db.commit()
        run_id: int = cur.lastrowid if cur.lastrowid is not None else -1
        return run_id

    def finish_run(self, run_id: int, outcome: RunOutcome) -> None:
        """Record run completion."""
        self._db.execute(
            "UPDATE runs SET finished=?, exit_code=?, books_done=?, books_failed=?, "
            "tokens_in=?, tokens_out=? WHERE id=?",
            (
                _now(),
                outcome.exit_code,
                outcome.books_done,
                outcome.books_failed,
                outcome.tokens_in,
                outcome.tokens_out,
                run_id,
            ),
        )
        self._db.commit()

    def last_runs(self, limit: int = 5) -> list[RunRecord]:
        """Most recent runs, newest first."""
        rows = self._db.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            RunRecord(
                id=row["id"],
                started=row["started"],
                finished=row["finished"],
                exit_code=row["exit_code"],
                books_done=row["books_done"],
                books_failed=row["books_failed"],
                tokens_in=row["tokens_in"],
                tokens_out=row["tokens_out"],
            )
            for row in rows
        ]

    def get_job(self, book_id: int, variant: Variant) -> Job | None:
        """Fetch one job, if any."""
        row = self._db.execute(
            "SELECT * FROM jobs WHERE book_id=? AND variant=?",
            (book_id, variant.value),
        ).fetchone()
        return _row_to_job(row) if row is not None else None

    def put_job(self, job: Job) -> None:
        """Insert or replace a job row (full-row upsert)."""
        self._db.execute(
            "INSERT INTO jobs (book_id, variant, status, attempts, source_hash, "
            "config_hash, last_chunk, started, finished, error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(book_id, variant) DO UPDATE SET status=excluded.status, "
            "attempts=excluded.attempts, source_hash=excluded.source_hash, "
            "config_hash=excluded.config_hash, last_chunk=excluded.last_chunk, "
            "started=excluded.started, finished=excluded.finished, error=excluded.error",
            (
                job.book_id,
                job.variant.value,
                job.status.value,
                job.attempts,
                job.source_hash,
                job.config_hash,
                job.last_chunk,
                job.started,
                job.finished,
                job.error,
            ),
        )
        self._db.commit()

    def jobs(self, statuses: Sequence[JobStatus] | None = None) -> list[Job]:
        """All jobs, optionally filtered by status."""
        if statuses:
            marks = ",".join("?" for _ in statuses)
            rows = self._db.execute(
                f"SELECT * FROM jobs WHERE status IN ({marks})",  # noqa: S608
                [s.value for s in statuses],
            ).fetchall()
        else:
            rows = self._db.execute("SELECT * FROM jobs").fetchall()
        return [_row_to_job(r) for r in rows]
