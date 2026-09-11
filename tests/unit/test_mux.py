"""Muxer: pure command construction + real ffmpeg encode smoke tests."""

from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from calibreaudiobridge.audio.mux import (
    AudioError,
    ChapterAudio,
    M4BSpec,
    build_m4b_command,
    build_mp3_command,
    run_ffmpeg,
    wav_duration_ms,
    write_concat_list,
    write_ffmetadata,
)
from calibreaudiobridge.config import AudioConfig

FFPROBE = shutil.which("ffprobe") or "ffprobe"
ffmpeg_available = shutil.which("ffmpeg") is not None


def _sine_wav(path: Path, seconds: float, freq: int = 220) -> Path:
    rate = 22050
    frames = int(rate * seconds)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        for i in range(frames):
            value = int(12000 * math.sin(2 * math.pi * freq * i / rate))
            writer.writeframesraw(struct.pack("<h", value))
    return path


def _probe(path: Path) -> dict[str, object]:
    result = subprocess.run(
        [FFPROBE, "-v", "quiet", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return dict(json.loads(result.stdout).get("format", {}))


class TestCommandBuilders_whenPure:
    def test_m4b_command_shape(self, tmp_path: Path) -> None:
        cfg = AudioConfig()
        cmd = build_m4b_command(
            M4BSpec(
                concat_list=tmp_path / "list.txt",
                ffmetadata=tmp_path / "meta.txt",
                output=tmp_path / "out.m4b",
                tags={"title": "Dune (Audiobook)"},
            ),
            cfg,
        )
        assert cmd[0] == "-y"
        assert "ipod" in cmd
        assert "-disposition:v" not in cmd
        assert "title=Dune (Audiobook)" in cmd

    def test_m4b_command_with_cover(self, tmp_path: Path) -> None:
        cmd = build_m4b_command(
            M4BSpec(
                concat_list=tmp_path / "l",
                ffmetadata=tmp_path / "m",
                output=tmp_path / "o.m4b",
                tags={},
                cover=tmp_path / "cover.jpg",
            ),
            AudioConfig(),
        )
        assert "attached_pic" in cmd
        assert "-c:v" in cmd
        assert "copy" in cmd

    def test_mp3_command_shape(self, tmp_path: Path) -> None:
        cmd = build_mp3_command(
            wav=tmp_path / "in.wav", output=tmp_path / "out.mp3",
            cfg=AudioConfig(), tags={"album": "X"},
        )
        assert "libmp3lame" in cmd
        assert "album=X" in cmd


class TestChapterMetadata_whenWritten:
    def test_ffmetadata_chapter_marks(self, tmp_path: Path) -> None:
        chapters = [
            ChapterAudio(title="One", wav=tmp_path / "1.wav", duration_ms=1000),
            ChapterAudio(title="Two", wav=tmp_path / "2.wav", duration_ms=2500),
        ]
        path = write_ffmetadata(chapters, tmp_path / "meta")
        text = path.read_text()
        assert "START=0" in text
        assert "END=1000" in text
        assert "START=1000" in text
        assert "END=3500" in text
        assert "title=Two" in text

    def test_concat_list_lines(self, tmp_path: Path) -> None:
        first, second = tmp_path / "a.wav", tmp_path / "b.wav"
        first.touch()
        second.touch()
        lines = write_concat_list([first, second], tmp_path / "list").read_text().splitlines()
        assert lines == [f"file '{first.resolve()}'", f"file '{second.resolve()}'"]


class TestDuration_whenMeasured:
    def test_wav_duration(self, tmp_path: Path) -> None:
        wav = _sine_wav(tmp_path / "t.wav", 0.5)
        assert 450 <= wav_duration_ms(wav) <= 550


@pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg not installed")
class TestEncoding_whenRealFfmpeg:
    def test_m4b_encode_produces_ipod_format(self, tmp_path: Path) -> None:
        cfg = AudioConfig()
        chapters = [
            ChapterAudio("One", _sine_wav(tmp_path / "1.wav", 0.4), 400),
            ChapterAudio("Two", _sine_wav(tmp_path / "2.wav", 0.4), 400),
        ]
        concat = write_concat_list([c.wav for c in chapters], tmp_path / "list")
        meta = write_ffmetadata(chapters, tmp_path / "meta")
        out = tmp_path / "book.m4b"
        run_ffmpeg(
            cfg,
            build_m4b_command(
                M4BSpec(
                    concat_list=concat, ffmetadata=meta, output=out,
                    tags={"title": "Test (Audiobook)"},
                ),
                cfg,
            ),
        )
        assert out.is_file()
        assert out.stat().st_size > 1000
        fmt = _probe(out)
        assert "mp4" in str(fmt.get("format_name", ""))

    def test_mp3_encode_carries_tags(self, tmp_path: Path) -> None:
        cfg = AudioConfig()
        wav = _sine_wav(tmp_path / "s.wav", 0.4)
        out = tmp_path / "summary.mp3"
        run_ffmpeg(
            cfg,
            build_mp3_command(wav=wav, output=out, cfg=cfg, tags={"title": "Test (Summary)"}),
        )
        tags_value = _probe(out).get("tags", {})
        assert isinstance(tags_value, dict)
        tags = {str(k): str(v) for k, v in tags_value.items()}
        assert tags.get("title") == "Test (Summary)"

    def test_ffmpeg_failure_raises_audio_error(self, tmp_path: Path) -> None:
        bad = build_mp3_command(
            wav=tmp_path / "missing.wav", output=tmp_path / "x.mp3",
            cfg=AudioConfig(), tags={},
        )
        with pytest.raises(AudioError):
            run_ffmpeg(AudioConfig(), bad)
