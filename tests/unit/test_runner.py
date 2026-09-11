"""Runner: budget, book cap, exit codes, lock, idempotency."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from calibreaudiobridge.config import Config
from calibreaudiobridge.errors import LockHeldError
from calibreaudiobridge.lock import InstanceLock
from calibreaudiobridge.pipeline.runner import run_once
from calibreaudiobridge.tts import StubEngine
from tests.conftest import FAKES_DIR, build_oeb, make_book, mock_llm_http


def _config(
    tmp_path: Path,
    books: list[dict[str, object]],
    *,
    max_books: int = 10,
    time_budget_minutes: int = 240,
    llm_models: bool = True,
) -> Config:
    (tmp_path / "books.json").write_text(json.dumps(books))
    return Config.model_validate(
        {
            "calibre": {
                "library_path": str(tmp_path / "lib"),
                "calibredb_path": str(FAKES_DIR / "calibredb"),
                "ebook_convert_path": str(FAKES_DIR / "ebook-convert"),
            },
            "llm": {
                "models": (
                    [{"name": "mock", "base_url": "http://mock/v1", "max_context_tokens": 8192}]
                    if llm_models
                    else []
                )
            },
            "tts": {"routing": {"en": ["stub"], "default": ["stub"]}, "voices": {"en": "stub_en"}},
            "pipeline": {
                "max_books_per_run": max_books,
                "time_budget_minutes": time_budget_minutes,
                "work_dir": str(tmp_path / "state"),
            },
        }
    )


def _wire_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, drm: bool = False
) -> None:
    source = tmp_path / "source.epub"
    source.write_bytes(b"epub")
    monkeypatch.setenv("FAKE_CALIBRE_EXPORT_SRC", str(source))
    monkeypatch.setenv("FAKE_CALIBRE_BOOKS", str(tmp_path / "books.json"))
    if drm:
        monkeypatch.setenv("FAKE_EBOOKCONVERT_DRM", "1")
        return
    chapters = [
        ("Alpha", [" ".join(f"alpha{i}" for i in range(120))]),
        ("Beta", [" ".join(f"beta{i}" for i in range(120))]),
    ]
    monkeypatch.setenv("FAKE_EBOOKCONVERT_OEB", str(build_oeb(tmp_path / "oeb", chapters)))


class TestRunOnce_whenHappyPath:
    def test_processes_book_and_exits_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _config(tmp_path, [make_book(1)])
        _wire_extraction(tmp_path, monkeypatch)
        with mock_llm_http() as http:
            stats = run_once(cfg, engine_factory=lambda _lang: StubEngine(), http=http)
        assert stats.exit_code == 0
        assert stats.processed == 1
        assert stats.books_done == 1

    def test_second_run_is_idempotent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _config(tmp_path, [make_book(1)])
        _wire_extraction(tmp_path, monkeypatch)
        def factory(_lang: str) -> StubEngine:
            return StubEngine()
        with mock_llm_http() as http:
            first = run_once(cfg, engine_factory=factory, http=http)
            second = run_once(cfg, engine_factory=factory, http=http)
        assert first.processed == 1
        assert second.processed == 0
        assert second.books_done == 0
        assert second.exit_code == 0


class TestRunOnce_whenLimited:
    def test_time_budget_stops_before_first_book(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _config(tmp_path, [make_book(1), make_book(2)], time_budget_minutes=1)
        _wire_extraction(tmp_path, monkeypatch)
        ticks: Iterator[int] = iter([0, 100_000])
        stats = run_once(cfg, engine_factory=lambda _l: StubEngine(), clock=lambda: next(ticks))
        assert stats.processed == 0
        assert stats.remaining == 2

    def test_max_books_caps_processing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _config(tmp_path, [make_book(1), make_book(2)], max_books=1)
        _wire_extraction(tmp_path, monkeypatch)
        with mock_llm_http() as http:
            stats = run_once(cfg, engine_factory=lambda _l: StubEngine(), http=http)
        assert stats.processed == 1
        assert stats.remaining == 1


class TestRunOnce_whenFailing:
    def test_summary_failure_yields_exit_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _config(tmp_path, [make_book(1)], llm_models=False)
        _wire_extraction(tmp_path, monkeypatch)
        stats = run_once(cfg, engine_factory=lambda _l: StubEngine())
        assert stats.exit_code == 1
        assert stats.books_failed == 1

    def test_drm_book_is_skipped_not_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _config(tmp_path, [make_book(1, formats="MOBI")])
        _wire_extraction(tmp_path, monkeypatch, drm=True)
        stats = run_once(cfg, engine_factory=lambda _l: StubEngine())
        assert stats.exit_code == 0
        assert stats.books_done == 1


class TestRunOnce_whenLocked:
    def test_lock_held_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = _config(tmp_path, [make_book(1)])
        _wire_extraction(tmp_path, monkeypatch)
        cfg.pipeline.work_dir.mkdir(parents=True, exist_ok=True)
        with InstanceLock(cfg.pipeline.work_dir / "cab.lock"), pytest.raises(LockHeldError):
            run_once(cfg, engine_factory=lambda _l: StubEngine())
