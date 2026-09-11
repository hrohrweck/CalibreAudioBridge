"""Piper adapter: voice discovery, argv contract, error mapping via fake binary."""

from __future__ import annotations

import json
import wave
from dataclasses import dataclass
from pathlib import Path

import pytest

from calibreaudiobridge.config import TTSConfig
from calibreaudiobridge.tts.base import TTSSynthesisError, TTSUnavailableError
from calibreaudiobridge.tts.piper import PiperEngine
from tests.conftest import FAKES_DIR

FAKE_PIPER = FAKES_DIR / "piper"


@dataclass(frozen=True, slots=True)
class _PiperCall:
    """One recorded fake-piper invocation."""

    argv: list[str]
    model: str
    output_file: str
    text: str


def _models(tmp_path: Path) -> Path:
    models = tmp_path / "models"
    models.mkdir()
    for stem in ("de_DE-thorsten-high", "en_US-lessac-high", "en_GB-alan-low", "fr_FR-siwis"):
        (models / f"{stem}.onnx").touch()
    return models


def _cfg(models: Path | None, *, piper_path: str | None = None) -> TTSConfig:
    return TTSConfig(piper_path=piper_path or str(FAKE_PIPER), piper_model_dir=models)


def _calls(tmp_path: Path) -> list[_PiperCall]:
    log = tmp_path / "piper-log.jsonl"
    if not log.exists():
        return []
    return [_PiperCall(**json.loads(line)) for line in log.read_text().splitlines()]


class TestPiperVoices_whenModelsPresent:
    def test_lang_mapped_stems_sorted(self, tmp_path: Path) -> None:
        engine = PiperEngine(_cfg(_models(tmp_path)))
        assert engine.voices("de") == ["de_DE-thorsten-high"]
        assert engine.voices("en") == ["en_GB-alan-low", "en_US-lessac-high"]

    def test_unmapped_lang_has_no_voices(self, tmp_path: Path) -> None:
        assert PiperEngine(_cfg(_models(tmp_path))).voices("fr") == []


class TestPiperVoices_whenNoModelDir:
    def test_none_or_missing_dir_yields_empty_list(self, tmp_path: Path) -> None:
        assert PiperEngine(_cfg(None)).voices("en") == []
        assert PiperEngine(_cfg(tmp_path / "does-not-exist")).voices("en") == []


class TestPiperAvailability_whenBinaryProbed:
    def test_constructor_does_not_check_and_is_available_reports_path(self, tmp_path: Path) -> None:
        engine = PiperEngine(_cfg(_models(tmp_path)))
        assert engine.is_available()

    def test_missing_binary_is_not_available_without_raising(self, tmp_path: Path) -> None:
        engine = PiperEngine(_cfg(_models(tmp_path), piper_path="/nonexistent/cab-piper"))
        assert not engine.is_available()


class TestPiperSynthesize_whenFakeBinarySucceeds:
    def test_writes_valid_wav_and_passes_model_and_text(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("FAKE_PIPER_LOG", str(tmp_path / "piper-log.jsonl"))
        models = _models(tmp_path)
        engine = PiperEngine(_cfg(models))
        out_path = tmp_path / "out.wav"
        result = engine.synthesize(
            "Hallo Welt.", voice="de_DE-thorsten-high", lang="de", speed=1.0, out_path=out_path
        )
        assert result == out_path
        with wave.open(str(out_path), "rb") as wav:
            assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 22050)
        calls = _calls(tmp_path)
        assert len(calls) == 1
        assert calls[0].model == str(models / "de_DE-thorsten-high.onnx")
        assert calls[0].text == "Hallo Welt.\n"

    def test_speed_scales_length_scale(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("FAKE_PIPER_LOG", str(tmp_path / "piper-log.jsonl"))
        engine = PiperEngine(_cfg(_models(tmp_path)))
        for out_name, speed in (("out-1.wav", 1.0), ("out-15.wav", 1.5)):
            engine.synthesize(
                "Satz.", voice="de_DE-thorsten-high", lang="de", speed=speed,
                out_path=tmp_path / out_name,
            )
        calls = _calls(tmp_path)
        assert "--length-scale" not in calls[0].argv
        assert "--length-scale" in calls[1].argv
        argv = calls[1].argv
        assert argv[argv.index("--length-scale") + 1] == "0.666667"


class TestPiperSynthesize_whenModelMissing:
    def test_raises_unavailable_before_invoking_binary(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("FAKE_PIPER_LOG", str(tmp_path / "piper-log.jsonl"))
        engine = PiperEngine(_cfg(_models(tmp_path)))
        with pytest.raises(TTSUnavailableError) as excinfo:
            engine.synthesize(
                "Text.", voice="de_DE-gone", lang="de", speed=1.0, out_path=tmp_path / "x.wav"
            )
        assert excinfo.value.engine == "piper"
        assert _calls(tmp_path) == []


class TestPiperSynthesize_whenBinaryMissing:
    def test_raises_unavailable_on_filenotfound(self, tmp_path: Path) -> None:
        engine = PiperEngine(_cfg(_models(tmp_path), piper_path="/nonexistent/cab-piper"))
        with pytest.raises(TTSUnavailableError) as excinfo:
            engine.synthesize(
                "Text.", voice="de_DE-thorsten-high", lang="de", speed=1.0,
                out_path=tmp_path / "x.wav",
            )
        assert excinfo.value.engine == "piper"


class TestPiperSynthesize_whenBinaryFails:
    def test_raises_synthesis_error_with_stderr_tail(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("FAKE_PIPER_FAIL", "1")
        engine = PiperEngine(_cfg(_models(tmp_path)))
        with pytest.raises(TTSSynthesisError) as excinfo:
            engine.synthesize(
                "Text.", voice="de_DE-thorsten-high", lang="de", speed=1.0,
                out_path=tmp_path / "x.wav",
            )
        assert excinfo.value.engine == "piper"
        assert "fake piper" in excinfo.value.detail
