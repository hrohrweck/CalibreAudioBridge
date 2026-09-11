"""CLI surface: help, status, scan, bootstrap (M0/M1 acceptance)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from calibreaudiobridge.cli import app
from tests.conftest import FAKES_DIR

runner = CliRunner()


def _env_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CAB__CALIBRE__LIBRARY_PATH", str(tmp_path / "lib"))
    monkeypatch.setenv("CAB__CALIBRE__CALIBREDB_PATH", str(FAKES_DIR / "calibredb"))
    monkeypatch.setenv("CAB__CALIBRE__EBOOK_CONVERT_PATH", str(FAKES_DIR / "ebook-convert"))
    monkeypatch.setenv("CAB__PIPELINE__WORK_DIR", str(tmp_path / "state"))


class TestCli_whenHelp:
    def test_help_exits_zero(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "scan" in result.output


class TestCli_whenStatus:
    def test_status_runs_with_env_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_calibre
    ) -> None:
        _env_config(monkeypatch, tmp_path)
        fake_calibre()
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "calibre mode" in result.output

    def test_status_without_config_fails_with_code_2(self) -> None:
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 2


class TestCli_whenScan:
    def test_scan_json_lists_candidates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_calibre, book_row
    ) -> None:
        _env_config(monkeypatch, tmp_path)
        cfg = fake_calibre([book_row(1, title="Dune"), book_row(2, formats="EPUB, M4B, MP3")])
        del cfg
        result = runner.invoke(app, ["scan", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert [b["id"] for b in payload] == [1]
        assert payload[0]["needs_audiobook"] is True

    def test_scan_table_shows_title(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_calibre, book_row
    ) -> None:
        _env_config(monkeypatch, tmp_path)
        fake_calibre([book_row(1, title="Dune")])
        result = runner.invoke(app, ["scan"])
        assert result.exit_code == 0
        assert "Dune" in result.output


class TestCli_whenBootstrap:
    def test_bootstrap_creates_columns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_calibre
    ) -> None:
        _env_config(monkeypatch, tmp_path)
        fake_calibre(custom_columns="missing")
        result = runner.invoke(app, ["bootstrap"])
        assert result.exit_code == 0
        assert "cab_audiobook_status" in result.output


class TestCli_whenUnimplemented:
    def test_run_reports_milestone(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _env_config(monkeypatch, tmp_path)
        result = runner.invoke(app, ["run"])
        assert result.exit_code == 2
