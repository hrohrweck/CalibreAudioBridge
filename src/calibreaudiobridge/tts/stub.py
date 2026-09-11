"""Deterministic stub engine for tests and dry runs (zero dependencies)."""

from __future__ import annotations

import wave
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

SAMPLE_RATE: Final = 22050
SAMPLE_WIDTH: Final = 2
MS_PER_10_CHARS: Final = 100
MIN_DURATION_MS: Final = 50
TONE_HZ: Final = 220
AMPLITUDE: Final = 3000


class StubEngine:
    """Writes a quiet square wave; duration tracks text length.

    Same text produces a byte-identical file (no timestamps, no randomness);
    `voice`, `lang` and `speed` are accepted for protocol conformance only.
    """

    name: str = "stub"

    def voices(self, lang: str) -> list[str]:
        """One synthetic voice per language."""
        return [f"stub_{lang}"]

    def synthesize(
        self, text: str, *, voice: str, lang: str, speed: float, out_path: Path
    ) -> Path:
        """~100 ms per 10 characters of text (minimum 50 ms), 22050 Hz 16-bit mono."""
        _ = (voice, lang, speed)  # protocol conformance: one fixed voice, fixed tempo
        duration_ms = max(MIN_DURATION_MS, len(text) // 10 * MS_PER_10_CHARS)
        half_period = max(SAMPLE_RATE // TONE_HZ // 2, 1)
        frames = bytearray()
        for i in range(_ms_to_frames(duration_ms)):
            level = AMPLITUDE if (i // half_period) % 2 == 0 else -AMPLITUDE
            frames += level.to_bytes(SAMPLE_WIDTH, "little", signed=True)
        with wave.open(str(out_path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(SAMPLE_WIDTH)
            out.setframerate(SAMPLE_RATE)
            out.writeframes(bytes(frames))
        return out_path


def _ms_to_frames(duration_ms: int) -> int:
    return duration_ms * SAMPLE_RATE // 1000
