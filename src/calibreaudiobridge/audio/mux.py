"""ffmpeg muxing: chapter concat, loudness normalization, M4B/MP3 encoding.

Command construction is pure (unit-tested); only run_ffmpeg touches the system.
"""

from __future__ import annotations

import subprocess
import wave
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ..errors import CabError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ..config import AudioConfig

CHAPTER_TIMEBASE_MS: Final = 1000


class AudioError(CabError):
    """ffmpeg failed or produced no output."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"audio packaging failed: {detail}")
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ChapterAudio:
    """One rendered chapter ready for muxing."""

    title: str
    wav: Path
    duration_ms: int


def wav_duration_ms(wav_path: Path) -> int:
    """Duration of a WAV file in milliseconds (stdlib wave reader)."""
    with wave.open(str(wav_path), "rb") as reader:
        return round(reader.getnframes() / reader.getframerate() * CHAPTER_TIMEBASE_MS)


def write_concat_list(wavs: Sequence[Path], list_path: Path) -> Path:
    """Write an ffmpeg concat-demuxer list file."""
    lines = [f"file '{wav.resolve()}'" for wav in wavs]
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list_path


def write_ffmetadata(chapters: Sequence[ChapterAudio], path: Path) -> Path:
    """Write an ffmetadata file with chapter marks derived from durations."""
    lines = [";FFMETADATA1"]
    start_ms = 0
    for chapter in chapters:
        end_ms = start_ms + chapter.duration_ms
        lines.extend(
            [
                "[CHAPTER]",
                f"TIMEBASE=1/{CHAPTER_TIMEBASE_MS}",
                f"START={start_ms}",
                f"END={end_ms}",
                f"title={chapter.title}",
            ]
        )
        start_ms = end_ms
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@dataclass(frozen=True, slots=True)
class M4BSpec:
    """Inputs for one audiobook M4B encode."""

    concat_list: Path
    ffmetadata: Path
    output: Path
    tags: dict[str, str]
    cover: Path | None = None


def build_m4b_command(spec: M4BSpec, cfg: AudioConfig) -> list[str]:
    """Assemble the ffmpeg argv for the audiobook M4B (pure function)."""
    cmd = ["-y", "-f", "concat", "-safe", "0", "-i", str(spec.concat_list)]
    cmd += ["-i", str(spec.ffmetadata)]
    if spec.cover is not None:
        cmd += ["-i", str(spec.cover)]
    cmd += ["-map", "0:a", "-map_metadata", "1"]
    if spec.cover is not None:
        cmd += ["-map", "2:v", "-c:v", "copy", "-disposition:v", "attached_pic"]
    cmd += [
        "-c:a", "aac", "-b:a", cfg.m4b_bitrate, "-ar", "44100", "-ac", "1",
        "-af", f"loudnorm={cfg.loudnorm_target}",
        "-f", "ipod",
    ]
    cmd += _tag_args(spec.tags)
    cmd.append(str(spec.output))
    return cmd


def build_mp3_command(
    *, wav: Path, output: Path, cfg: AudioConfig, tags: dict[str, str]
) -> list[str]:
    """Assemble the ffmpeg argv for the summary MP3 (pure function)."""
    cmd = [
        "-y", "-i", str(wav),
        "-c:a", "libmp3lame", "-b:a", cfg.mp3_bitrate, "-ar", "44100", "-ac", "1",
        "-af", f"loudnorm={cfg.loudnorm_target}",
    ]
    cmd += _tag_args(tags)
    cmd.append(str(output))
    return cmd


def _tag_args(tags: dict[str, str]) -> list[str]:
    args: list[str] = []
    for key, value in tags.items():
        args += ["-metadata", f"{key}={value}"]
    return args


def run_ffmpeg(cfg: AudioConfig, args: Sequence[str]) -> None:
    """Run ffmpeg, raising AudioError with the stderr tail on failure."""
    cmd = [cfg.ffmpeg_path, *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, check=False)
    except FileNotFoundError:
        raise AudioError(f"ffmpeg not found: {cfg.ffmpeg_path}") from None
    except subprocess.TimeoutExpired:
        raise AudioError("ffmpeg timed out after 1800s") from None
    if result.returncode != 0:
        stderr = (result.stderr or "").strip().splitlines()[-5:]
        raise AudioError("; ".join(stderr))
