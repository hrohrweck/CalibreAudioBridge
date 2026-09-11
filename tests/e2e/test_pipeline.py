"""End-to-end: fake calibre + fake ebook-convert + mock LLM + stub TTS + real ffmpeg."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from calibreaudiobridge.calibre.client import CalibreClient
from calibreaudiobridge.calibre.scanner import select_candidates
from calibreaudiobridge.config import Config
from calibreaudiobridge.enums import JobStatus
from calibreaudiobridge.pipeline.orchestrator import Orchestrator
from calibreaudiobridge.state import Ledger
from calibreaudiobridge.tts import StubEngine
from tests.conftest import FAKES_DIR, build_oeb, make_book, mock_llm_http

ffmpeg_available = shutil.which("ffmpeg") is not None

CHAPTERS: list[tuple[str, list[str]]] = [
    ("Alpha", [" ".join(f"alpha{i}" for i in range(120))]),
    ("Beta", [" ".join(f"beta{i}" for i in range(120))]),
]


def _config(tmp_path: Path) -> Config:
    return Config.model_validate(
        {
            "calibre": {
                "library_path": str(tmp_path / "lib"),
                "calibredb_path": str(FAKES_DIR / "calibredb"),
                "ebook_convert_path": str(FAKES_DIR / "ebook-convert"),
            },
            "llm": {"models": [{"name": "mock", "base_url": "http://mock/v1"}]},
            "tts": {"routing": {"en": ["stub"], "default": ["stub"]}},
            "pipeline": {"work_dir": str(tmp_path / "state")},
        }
    )


@pytest.fixture
def wired_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CALIBRE_LOG", str(log))
    (tmp_path / "books.json").write_text(json.dumps([make_book(1, title="Dune")]))
    monkeypatch.setenv("FAKE_CALIBRE_BOOKS", str(tmp_path / "books.json"))
    source = tmp_path / "source.epub"
    source.write_bytes(b"epub")
    monkeypatch.setenv("FAKE_CALIBRE_EXPORT_SRC", str(source))
    monkeypatch.setenv("FAKE_EBOOKCONVERT_OEB", str(build_oeb(tmp_path / "oeb", CHAPTERS)))
    return log


@pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg not installed")
class TestPipeline_whenFullRun:
    def test_audiobook_and_summary_attach_and_mark_done(
        self, tmp_path: Path, wired_book: Path
    ) -> None:
        cfg = _config(tmp_path)
        client = CalibreClient(cfg.calibre)
        (candidate,) = select_candidates(client.list_books(), restrict_tag="")
        with (
            Ledger(cfg.pipeline.work_dir / "ledger.db") as ledger,
            mock_llm_http() as http,
        ):
            orchestrator = Orchestrator(
                cfg=cfg, client=client, ledger=ledger, http=http,
                engine_factory=lambda _lang: StubEngine(),
            )
            outcome = orchestrator.process(candidate)
        assert [o.status for o in outcome.outcomes] == [JobStatus.DONE, JobStatus.DONE]

        calls = [json.loads(line) for line in wired_book.read_text().splitlines()]
        added = [c for c in calls if c["sub"] == "add_format"]
        assert any(str(a["opts"][1]).endswith(".m4b") for a in added)
        assert any(str(a["opts"][1]).endswith(".mp3") for a in added)
        statuses = [c["opts"] for c in calls if c["sub"] == "set_custom"]
        assert ["cab_audiobook_status", "1", "done"] in statuses
        assert ["cab_summary_status", "1", "done"] in statuses

        m4b = next(
            Path(a["opts"][1]) for a in added if str(a["opts"][1]).endswith(".m4b")
        )
        assert m4b.is_file()
        assert m4b.stat().st_size > 1000

    def test_second_process_skips_completed_work(
        self, tmp_path: Path, wired_book: Path
    ) -> None:
        cfg = _config(tmp_path)
        client = CalibreClient(cfg.calibre)
        (candidate,) = select_candidates(client.list_books(), restrict_tag="")
        with (
            Ledger(cfg.pipeline.work_dir / "ledger.db") as ledger,
            mock_llm_http() as http,
        ):
            orchestrator = Orchestrator(
                cfg=cfg, client=client, ledger=ledger, http=http,
                engine_factory=lambda _lang: StubEngine(),
            )
            first = orchestrator.process(candidate)
            lines_before = len(wired_book.read_text().splitlines())
            second = orchestrator.process(candidate)
        assert all(o.status is JobStatus.DONE for o in first.outcomes)
        assert [o.detail for o in second.outcomes] == ["already done", "already done"]
        assert len(wired_book.read_text().splitlines()) == lines_before
