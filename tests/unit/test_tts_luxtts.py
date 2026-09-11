"""LuxTTS adapter: lazy-import guard + voice catalog tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from calibreaudiobridge.config import TTSConfig
from calibreaudiobridge.tts.base import TTSUnavailableError
from calibreaudiobridge.tts.luxtts import SUPPORTED_LANGS, LuxTTSEngine


class TestLuxTTSEngine_whenUnavailable:
    def test_raises_when_no_reference_wav(self) -> None:
        cfg = TTSConfig()
        with pytest.raises(TTSUnavailableError, match="luxtts"):
            LuxTTSEngine(cfg)

    def test_raises_when_reference_wav_missing(self, tmp_path: Path) -> None:
        cfg = TTSConfig()
        object.__setattr__(cfg.luxtts, "reference_wav", tmp_path / "missing.wav")
        with pytest.raises(TTSUnavailableError, match="luxtts"):
            LuxTTSEngine(cfg)

    def test_raises_when_zipvoice_not_installed(self, tmp_path: Path) -> None:
        ref = tmp_path / "narrator.wav"
        ref.write_bytes(b"\x00" * 16)
        cfg = TTSConfig()
        object.__setattr__(cfg.luxtts, "reference_wav", ref)
        with pytest.raises(TTSUnavailableError, match="luxtts"):
            LuxTTSEngine(cfg)


class TestLuxTTSEngine_langSupport:
    def test_english_is_supported(self) -> None:
        assert "en" in SUPPORTED_LANGS

    def test_german_is_not_supported(self) -> None:
        assert "de" not in SUPPORTED_LANGS
