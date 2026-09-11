"""Engine routing: first available engine in the language chain wins."""

from __future__ import annotations

from pathlib import Path

import pytest

from calibreaudiobridge.config import TTSConfig
from calibreaudiobridge.pipeline.engines import resolve_engine
from calibreaudiobridge.tts import PiperEngine, StubEngine, TTSUnavailableError


class TestResolveEngine_whenStubRouted:
    def test_returns_stub(self) -> None:
        cfg = TTSConfig(routing={"en": ["stub"]})
        engine = resolve_engine(cfg, "en")
        assert isinstance(engine, StubEngine)


class TestResolveEngine_whenChainFallsThrough:
    def test_skips_kokoro_without_extra(self) -> None:
        cfg = TTSConfig(routing={"en": ["kokoro", "stub"]})
        engine = resolve_engine(cfg, "en")
        assert isinstance(engine, StubEngine)

    def test_unavailable_when_chain_empty(self) -> None:
        cfg = TTSConfig(routing={"en": ["piper"]}, piper_path="nonexistent-piper")
        with pytest.raises(TTSUnavailableError):
            resolve_engine(cfg, "en")

    def test_unknown_lang_uses_default_chain(self) -> None:
        cfg = TTSConfig(routing={"default": ["stub"]})
        assert isinstance(resolve_engine(cfg, "fr"), StubEngine)


class TestResolveEngine_whenPiperAvailable:
    def test_returns_piper_when_binary_and_models_exist(self, tmp_path: Path) -> None:
        model = tmp_path / "en_US-lessac-high.onnx"
        model.write_bytes(b"onnx")
        cfg = TTSConfig(
            routing={"en": ["piper"]},
            piper_path=str(tmp_path / "piper-bin"),
            piper_model_dir=tmp_path,
        )
        (tmp_path / "piper-bin").write_text("#!/bin/sh\nexit 0\n")
        (tmp_path / "piper-bin").chmod(0o755)
        engine = resolve_engine(cfg, "en")
        assert isinstance(engine, PiperEngine)
        assert "en_US-lessac-high" in engine.voices("en")
