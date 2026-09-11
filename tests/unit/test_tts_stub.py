"""Stub engine: deterministic WAV output for tests and dry runs."""

from __future__ import annotations

import wave
from pathlib import Path

from calibreaudiobridge.tts.stub import SAMPLE_RATE, SAMPLE_WIDTH, StubEngine


def _read(path: Path) -> tuple[tuple[int, int, int], int]:
    with wave.open(str(path), "rb") as wav:
        return (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), wav.getnframes()


class TestStubEngine_whenAskedForVoices:
    def test_one_voice_per_language(self) -> None:
        assert StubEngine().voices("de") == ["stub_de"]


class TestStubEngine_whenSynthesizing:
    def test_writes_valid_mono_16bit_22050_wav(self, tmp_path: Path) -> None:
        out_path = tmp_path / "stub.wav"
        result = StubEngine().synthesize(
            "Hello world, this is a test!", voice="stub_en", lang="en", speed=1.0,
            out_path=out_path,
        )
        assert result == out_path
        params, _ = _read(out_path)
        assert params == (1, SAMPLE_WIDTH, SAMPLE_RATE)

    def test_duration_tracks_text_length(self, tmp_path: Path) -> None:
        # 28 characters -> 2 full 10-char blocks -> 200 ms
        _, nframes = _read(_synth(tmp_path / "a.wav", "x" * 28))
        assert nframes == 200 * SAMPLE_RATE // 1000

    def test_minimum_duration_for_short_text(self, tmp_path: Path) -> None:
        _, nframes = _read(_synth(tmp_path / "b.wav", "hi"))
        assert nframes == 50 * SAMPLE_RATE // 1000

    def test_audio_is_not_pure_silence(self, tmp_path: Path) -> None:
        path = _synth(tmp_path / "tone.wav", "square wave marker")
        with wave.open(str(path), "rb") as wav:
            frames = wav.readframes(wav.getnframes())
        samples = [int.from_bytes(frames[i : i + SAMPLE_WIDTH], "little", signed=True) for i in
                   range(0, len(frames), SAMPLE_WIDTH)]
        assert any(sample != 0 for sample in samples)


class TestStubEngine_whenCalledTwice:
    def test_same_text_produces_byte_identical_files(self, tmp_path: Path) -> None:
        first = _synth(tmp_path / "one.wav", "identical input")
        second = _synth(tmp_path / "two.wav", "identical input")
        assert first.read_bytes() == second.read_bytes()

    def test_different_length_text_produces_different_files(self, tmp_path: Path) -> None:
        short = _synth(tmp_path / "s.wav", "short")
        long = _synth(tmp_path / "l.wav", "a considerably longer piece of stub input text")
        assert short.read_bytes() != long.read_bytes()


def _synth(out_path: Path, text: str) -> Path:
    return StubEngine().synthesize(
        text, voice="stub_en", lang="en", speed=1.0, out_path=out_path
    )
