"""Cron run loop: lock, mode probe, budget, queue, exit codes (PLAN.md 9.2)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..calibre.client import CalibreClient
from ..calibre.scanner import select_candidates
from ..lock import InstanceLock
from ..state import Ledger, RunOutcome
from .orchestrator import Orchestrator

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

    from ..config import Config
    from ..tts import TTSEngine

Clock = Callable[[], float]


class Budget:
    """Wall-clock budget with an injectable clock for deterministic tests."""

    def __init__(self, minutes: int, clock: Clock = time.monotonic) -> None:
        self._clock = clock
        self._deadline = clock() + minutes * 60

    def expired(self) -> bool:
        """Report whether the budget is used up."""
        return self._clock() >= self._deadline


@dataclass(frozen=True, slots=True)
class RunStats:
    """One `cab run` summary."""

    exit_code: int
    processed: int
    books_done: int
    books_failed: int
    remaining: int


def run_once(
    cfg: Config,
    *,
    http: httpx.Client | None = None,
    engine_factory: Callable[[str], TTSEngine] | None = None,
    clock: Clock = time.monotonic,
) -> RunStats:
    """One full cron cycle. Raises CabError subclasses for env problems."""
    work_dir: Path = cfg.pipeline.work_dir
    work_dir.mkdir(parents=True, exist_ok=True)
    with InstanceLock(work_dir / "cab.lock"):
        client = CalibreClient(cfg.calibre)
        client.resolve_mode()
        candidates = select_candidates(
            client.list_books(), restrict_tag=cfg.calibre.restrict_tag
        )
        with Ledger(work_dir / "ledger.db") as ledger:
            orchestrator = Orchestrator(
                cfg=cfg, client=client, ledger=ledger, http=http, engine_factory=engine_factory
            )
            budget = Budget(cfg.pipeline.time_budget_minutes, clock)
            run_id = ledger.start_run()
            done = failed = processed = 0
            for index, candidate in enumerate(candidates):
                if processed >= cfg.pipeline.max_books_per_run or budget.expired():
                    remaining = len(candidates) - index
                    break
                outcome = orchestrator.process(candidate)
                if outcome.worked:
                    processed += 1
                    if outcome.all_succeeded():
                        done += 1
                    else:
                        failed += 1
            else:
                remaining = 0
            exit_code = 0 if failed == 0 else 1
            ledger.finish_run(run_id, RunOutcome(exit_code, done, failed))
    return RunStats(exit_code, processed, done, failed, remaining)
