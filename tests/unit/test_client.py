"""CalibreClient against the fake calibredb: probe, list, write ops, export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from calibreaudiobridge.calibre.client import CalibreClient, CalibreMode
from calibreaudiobridge.config import CalibreConfig
from calibreaudiobridge.errors import CalibreBlockedError, CalibreEnvError


class TestResolveMode_whenServerConfigured:
    def test_fragment_url_uses_server(self, fake_calibre) -> None:
        cfg = fake_calibre(server_url="http://localhost:9999/#mylib")
        client = CalibreClient(cfg)
        assert client.resolve_mode() == CalibreMode.SERVER
        assert client.list_books() == []

    def test_bare_url_discovers_library(self, fake_calibre) -> None:
        cfg = fake_calibre(server_url="http://localhost:9999")
        client = CalibreClient(cfg)
        assert client.resolve_mode() == CalibreMode.SERVER

    def test_unreachable_server_falls_back_to_local(
        self, fake_calibre, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_SERVER_DOWN", "1")
        cfg = fake_calibre(server_url="http://localhost:9999/#lib")
        client = CalibreClient(cfg)
        assert client.resolve_mode() == CalibreMode.LOCAL


class TestResolveMode_whenLocalOnly:
    def test_local_library_resolves(self, fake_calibre) -> None:
        assert CalibreClient(fake_calibre()).resolve_mode() == CalibreMode.LOCAL

    def test_locked_library_raises_blocked(self, fake_calibre) -> None:
        cfg = fake_calibre(lock=True)
        with pytest.raises(CalibreBlockedError):
            CalibreClient(cfg).resolve_mode()


class TestListBooks_whenParsing:
    def test_parses_formats_languages_statuses(self, fake_calibre, book_row) -> None:
        row = book_row(
            1,
            formats="EPUB, MOBI",
            languages="eng, deu",
            tags=["fiction"],
            audiobook_status="done",
        )
        client = CalibreClient(fake_calibre([row]))
        (parsed,) = client.list_books()
        assert parsed.formats == ("EPUB", "MOBI")
        assert parsed.languages == ("en", "de")
        assert parsed.tags == ("fiction",)
        assert parsed.audiobook_status == "done"
        assert parsed.summary_status == ""


class TestWriteOps_whenCalled:
    def test_add_format_and_set_custom_are_logged(
        self, fake_calibre, tmp_path, monkeypatch
    ) -> None:
        log = tmp_path / "calls.jsonl"
        monkeypatch.setenv("FAKE_CALIBRE_LOG", str(log))
        client = CalibreClient(fake_calibre())
        audio = tmp_path / "out.m4b"
        audio.write_bytes(b"data")
        client.add_format(42, audio)
        client.set_custom("cab_audiobook_status", 42, "done")
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        add = next(c for c in calls if c["sub"] == "add_format")
        assert add["opts"][:2] == ["42", str(audio)]
        setc = next(c for c in calls if c["sub"] == "set_custom")
        assert setc["opts"] == ["cab_audiobook_status", "42", "done"]

    def test_export_returns_file(self, fake_calibre, tmp_path, monkeypatch) -> None:
        src = tmp_path / "source.epub"
        src.write_bytes(b"epub")
        monkeypatch.setenv("FAKE_CALIBRE_EXPORT_SRC", str(src))
        client = CalibreClient(fake_calibre())
        exported = client.export(1, "EPUB", tmp_path / "out")
        assert exported.name == "source.epub"
        assert exported.read_bytes() == b"epub"


class TestEnvErrors_whenBroken:
    def test_missing_binary_raises_env_error(self, tmp_path: Path) -> None:
        cfg = CalibreConfig(
            library_path=tmp_path, calibredb_path=str(tmp_path / "nonexistent-calibredb")
        )
        with pytest.raises(CalibreEnvError):
            CalibreClient(cfg).resolve_mode()
