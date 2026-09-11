"""Kokoro-82M adapter, the default engine (PLAN.md D1).

`kokoro` and `soundfile` are optional extras: both load lazily inside
`__init__` so this module imports cleanly without them, and construction
raises the typed `TTSUnavailableError` instead.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Final

from .base import TTSSynthesisError, TTSUnavailableError

if TYPE_CHECKING:
    from pathlib import Path

    from ..config import TTSConfig

# Subset of the Kokoro-82M fixed voice catalog (a=US English, d=German).
KOKORO_VOICES: Final[dict[str, tuple[str, ...]]] = {
    "en": ("af_heart", "af_alloy", "am_fenrir", "am_michael"),
    "de": ("df_anna", "df_amadeus", "dm_arthur"),
    "other": ("af_heart",),
}

_KOKORO_LANG_CODES: Final[dict[str, str]] = {"en": "a", "de": "d"}
_DEFAULT_LANG_CODE: Final = "a"
_SAMPLE_RATE: Final = 24000


def kokoro_voices(lang: str) -> tuple[str, ...]:
    """Voice catalog entry for `lang`; unknown languages get the EN default."""
    return KOKORO_VOICES.get(lang, KOKORO_VOICES["other"])


class KokoroEngine:
    """KPipeline-backed engine; one pipeline is cached per kokoro lang code."""

    name: str = "kokoro"

    def __init__(self, cfg: TTSConfig) -> None:
        """Lazily import the heavy optional deps; ImportError maps to a typed error."""
        try:
            self._kokoro = importlib.import_module("kokoro")
            self._soundfile = importlib.import_module("soundfile")
        except ImportError as exc:
            raise TTSUnavailableError("kokoro") from exc
        self._cfg = cfg
        self._pipelines = {}

    def voices(self, lang: str) -> tuple[str, ...]:
        """Kokoro-82M fixed voice catalog (subset)."""
        return kokoro_voices(lang)

    def synthesize(
        self, text: str, *, voice: str, lang: str, speed: float, out_path: Path
    ) -> Path:
        """Stream KPipeline audio chunks into one 24 kHz mono WAV via soundfile."""
        code = _KOKORO_LANG_CODES.get(lang, _DEFAULT_LANG_CODE)
        pipeline = self._pipelines.get(code)
        if pipeline is None:
            pipeline = self._kokoro.KPipeline(lang_code=code)
            self._pipelines[code] = pipeline
        try:
            writer = self._soundfile.SoundFile(
                str(out_path), mode="w", samplerate=_SAMPLE_RATE, channels=1
            )
            with writer:
                for result in pipeline(text, voice=voice, speed=speed):
                    writer.write(result.audio.numpy())
        except (OSError, RuntimeError, ValueError) as exc:
            # kokoro (torch) and soundfile (libsndfile) both raise from these roots.
            raise TTSSynthesisError("kokoro", str(exc)) from exc
        return out_path
