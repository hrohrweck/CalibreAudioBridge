"""Config layering: defaults <- TOML <- env (PLAN.md D3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from calibreaudiobridge.config import config_hash, load_config
from calibreaudiobridge.errors import ConfigError


class TestLoadConfig_whenNoInputs:
    def test_raises_without_library_path(self) -> None:
        with pytest.raises(ConfigError):
            load_config(None)


class TestLoadConfig_whenTomlGiven:
    def test_loads_overrides(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "cab.toml"
        cfg_file.write_text(
            "[calibre]\nlibrary_path = '/tmp/lib'\n"
            "[summary]\ntarget_minutes = 15\n"
        )
        cfg = load_config(cfg_file)
        assert cfg.calibre.library_path == Path("/tmp/lib")
        assert cfg.summary.target_minutes == 15


class TestLoadConfig_whenEnvGiven:
    def test_env_overrides_toml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_file = tmp_path / "cab.toml"
        cfg_file.write_text(
            "[calibre]\nlibrary_path = '/tmp/lib'\n"
            "[summary]\ntarget_minutes = 15\n"
        )
        monkeypatch.setenv("CAB__SUMMARY__TARGET_MINUTES", "20")
        monkeypatch.setenv("CAB__TTS__SPEED", "1.5")
        cfg = load_config(cfg_file)
        assert cfg.summary.target_minutes == 20
        assert cfg.tts.speed == 1.5

    def test_invalid_env_value_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg_file = tmp_path / "cab.toml"
        cfg_file.write_text("[calibre]\nlibrary_path = '/tmp/lib'\n")
        monkeypatch.setenv("CAB__SUMMARY__TARGET_MINUTES", '"not an int"')
        with pytest.raises(ConfigError):
            load_config(cfg_file)

    def test_unknown_section_env_is_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg_file = tmp_path / "cab.toml"
        cfg_file.write_text("[calibre]\nlibrary_path = '/tmp/lib'\n")
        monkeypatch.setenv("CAB__NONSECTION__KEY", "1")
        cfg = load_config(cfg_file)
        assert cfg.calibre.library_path == Path("/tmp/lib")


class TestConfigHash:
    def test_stable_across_loads(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "cab.toml"
        cfg_file.write_text("[calibre]\nlibrary_path = '/tmp/lib'\n[tts]\nspeed = 1.1\n")
        assert config_hash(load_config(cfg_file)) == config_hash(load_config(cfg_file))

    def test_changes_with_voice(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "cab.toml"
        cfg_file.write_text("[calibre]\nlibrary_path = '/tmp/lib'\n")
        monkey = pytest.MonkeyPatch()
        monkey.setenv("CAB__TTS__SPEED", "1.0")
        before = config_hash(load_config(cfg_file))
        monkey.setenv("CAB__TTS__SPEED", "1.3")
        after = config_hash(load_config(cfg_file))
        monkey.undo()
        assert before != after
