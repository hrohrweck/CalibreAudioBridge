"""TTS layer: engine protocol, adapters (stub/piper/kokoro), long-text synthesis, cache."""

from __future__ import annotations

from .base import (
    Segment,
    TTSEngine,
    TTSSynthesisError,
    TTSUnavailableError,
    silence_wav,
    split_sentences,
    synthesize_long,
)
from .cache import TTSCache
from .kokoro import KOKORO_VOICES, KokoroEngine
from .piper import PIPER_LOCALES, PiperEngine
from .stub import StubEngine

__all__ = [
    "KOKORO_VOICES",
    "PIPER_LOCALES",
    "KokoroEngine",
    "PiperEngine",
    "Segment",
    "StubEngine",
    "TTSCache",
    "TTSEngine",
    "TTSSynthesisError",
    "TTSUnavailableError",
    "silence_wav",
    "split_sentences",
    "synthesize_long",
]
