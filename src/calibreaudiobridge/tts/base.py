"""TTS engine protocol, typed errors, and sentence-level long-text synthesis.

PLAN.md 7: engines are pluggable adapters behind `TTSEngine`; long texts are
synthesized sentence-by-sentence with per-sentence retries and silence
substitution so one bad sentence can never kill a whole book (PLAN.md 7.3).
"""

from __future__ import annotations

import logging
import re
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from ..errors import CabError

if TYPE_CHECKING:
    from collections.abc import Sequence

_LOG = logging.getLogger("cab.tts")

ATTEMPTS_PER_SENTENCE: Final = 3
FAILED_SENTENCE_SILENCE_MS: Final = 1000
DEFAULT_SAMPLE_RATE: Final = 22050
DEFAULT_SAMPLE_WIDTH: Final = 2

# Simple splitter (PLAN.md 7.3): boundaries at runs of .!?… plus optional
# closing quotes/brackets. Known limitation: no abbreviation/decimal handling.
_SENTENCE_RE: Final[re.Pattern[str]] = re.compile(r"[^.!?…]+(?:[.!?…]+[\"')\]]*)?")
_PARAGRAPH_RE: Final[re.Pattern[str]] = re.compile(r"\n\s*\n")


class TTSUnavailableError(CabError):
    """TTS engine, its binary, or a voice model is missing. Exit code 2."""

    engine: str

    def __init__(self, engine: str) -> None:
        super().__init__(f"TTS engine unavailable: {engine}")
        self.engine = engine


class TTSSynthesisError(CabError):
    """TTS synthesis failed (possibly after retries)."""

    engine: str
    detail: str

    def __init__(self, engine: str, detail: str) -> None:
        super().__init__(f"TTS synthesis failed ({engine}): {detail}")
        self.engine = engine
        self.detail = detail


class TTSEngine(Protocol):
    """One TTS adapter. `synthesize` writes a mono WAV to out_path."""

    name: str

    def voices(self, lang: str) -> Sequence[str]:
        """Return the voice names this engine offers for `lang`."""
        ...

    def synthesize(
        self, text: str, *, voice: str, lang: str, speed: float, out_path: Path
    ) -> Path:
        """Write `text` as mono audio to `out_path` and return it."""
        ...


@dataclass(frozen=True, slots=True)
class Segment:
    """One sentence; `ends_paragraph` selects the longer paragraph pause."""

    text: str
    ends_paragraph: bool


@dataclass(frozen=True, slots=True)
class _VoiceParams:
    """Voice/lang/speed triple shared by the sentence retry loop."""

    voice: str
    lang: str
    speed: float


def split_sentences(text: str) -> list[Segment]:
    """Split into sentences; paragraphs are blank-line separated blocks."""
    segments: list[Segment] = []
    for para in _PARAGRAPH_RE.split(text):
        sentences = [" ".join(match.split()) for match in _SENTENCE_RE.findall(para)]
        sentences = [s for s in sentences if s]
        if not sentences:
            continue
        segments.extend(Segment(text=s, ends_paragraph=False) for s in sentences[:-1])
        segments.append(Segment(text=sentences[-1], ends_paragraph=True))
    return segments


def silence_wav(
    path: Path, duration_ms: int, sample_rate: int = DEFAULT_SAMPLE_RATE, sample_width: int = 2
) -> Path:
    """Write `duration_ms` of mono silence as a valid WAV file at path."""
    frames = _silence_frames(_ms_to_frames(duration_ms, sample_rate), sample_width, channels=1)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(sample_width)
        out.setframerate(sample_rate)
        out.writeframes(frames)
    return path


def synthesize_long(  # noqa: PLR0913 -- fixed M3 interface; M4/M5 import this signature
    text: str,
    engine: TTSEngine,
    *,
    voice: str,
    lang: str,
    speed: float,
    sentence_pause_ms: int,
    paragraph_pause_ms: int,
    out_path: Path,
) -> Path:
    """Synthesize `text` sentence-by-sentence into one WAV at out_path.

    Each sentence gets `ATTEMPTS_PER_SENTENCE` tries; persistent failure is
    substituted with silence so the book survives (PLAN.md 7.3). Sentence and
    paragraph pauses are inserted as silence gaps between segments.
    """
    segments = split_sentences(text)
    if not segments:
        raise TTSSynthesisError(engine.name, "no sentences to synthesize")
    params = _VoiceParams(voice=voice, lang=lang, speed=speed)
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        sentence_wavs = [
            _synthesize_sentence(engine, segment.text, tmp / f"sentence_{i:06d}.wav", params)
            for i, segment in enumerate(segments)
        ]
        sample_rate, sample_width, channels = _wav_format(sentence_wavs[0])
        parts: list[Path] = []
        for index, segment in enumerate(segments):
            parts.append(sentence_wavs[index])
            if index == len(segments) - 1:
                break
            pause_ms = paragraph_pause_ms if segment.ends_paragraph else sentence_pause_ms
            if pause_ms > 0:
                parts.append(
                    silence_wav(tmp / f"pause_{index:06d}.wav", pause_ms, sample_rate, sample_width)
                )
        _concat_wavs(parts, out_path, channels=channels)
    return out_path


def _synthesize_sentence(
    engine: TTSEngine, text: str, dest: Path, params: _VoiceParams
) -> Path:
    """One sentence with retries; persistent failure becomes a silence substitute."""
    for _ in range(ATTEMPTS_PER_SENTENCE):
        try:
            return engine.synthesize(
                text, voice=params.voice, lang=params.lang, speed=params.speed, out_path=dest
            )
        except (TTSSynthesisError, TTSUnavailableError, OSError):
            continue
    _LOG.warning(
        "sentence failed after %d attempts, substituting %d ms silence: %r",
        ATTEMPTS_PER_SENTENCE,
        FAILED_SENTENCE_SILENCE_MS,
        text[:80],
    )
    return silence_wav(dest, FAILED_SENTENCE_SILENCE_MS)


def _concat_wavs(parts: Sequence[Path], out_path: Path, *, channels: int) -> None:
    """Concatenate WAVs; params come from the first part, mismatched parts go silent."""
    sample_rate, sample_width, _ = _wav_format(parts[0])
    with wave.open(str(out_path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(sample_width)
        out.setframerate(sample_rate)
        for part in parts:
            with wave.open(str(part), "rb") as source:
                if _wav_format(part) == (sample_rate, sample_width, channels):
                    out.writeframes(source.readframes(source.getnframes()))
                else:
                    out.writeframes(_silence_frames(source.getnframes(), sample_width, channels))


def _wav_format(path: Path) -> tuple[int, int, int]:
    with wave.open(str(path), "rb") as source:
        return source.getframerate(), source.getsampwidth(), source.getnchannels()


def _ms_to_frames(duration_ms: int, sample_rate: int) -> int:
    return duration_ms * sample_rate // 1000


def _silence_frames(nframes: int, sample_width: int, channels: int) -> bytes:
    return bytes(nframes * channels * sample_width)
