"""LuxTTS adapter — English-only zero-shot voice cloning (PLAN.md 7.2, D1).

LuxTTS is research-grade (no PyPI, no releases); imported lazily so the rest of
the pipeline works without it installed. Routed only to ``lang=en`` books via
``tts.routing``. Falls back to the next engine on import failure.

Install::

    git clone https://github.com/ysharma3501/LuxTTS
    cd LuxTTS && pip install -r requirements.txt
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Final

from .base import TTSSynthesisError, TTSUnavailableError

if TYPE_CHECKING:
    from ..config import TTSConfig

_SAMPLE_RATE: Final = 48000
_SAMPLE_WIDTH: Final = 2
SUPPORTED_LANGS: Final = frozenset({"en"})
_FALLBACK_VOICE: Final = "default"


class LuxTTSEngine:
    """Zero-shot voice cloning via a narrator reference WAV (≥ 3 s)."""

    name: str = "luxtts"

    def __init__(self, cfg: TTSConfig) -> None:
        ref = cfg.luxtts.reference_wav
        if ref is None:
            raise TTSUnavailableError("luxtts")
        self._ref = Path(ref).expanduser()
        if not self._ref.is_file():
            raise TTSUnavailableError("luxtts")
        try:
            _mod = importlib.import_module("zipvoice.luxvoice")
            self._cls = _mod.LuxTTS
        except (ImportError, AttributeError) as exc:
            raise TTSUnavailableError("luxtts") from exc
        self._cfg = cfg
        self._lux = None

    def voices(self, lang: str) -> list[str]:
        """LuxTTS has one voice per reference wav; returns the stem as the id."""
        if lang not in SUPPORTED_LANGS:
            return []
        return [self._ref.stem]

    def synthesize(self, text: str, *, voice: str, lang: str, speed: float, out_path: Path) -> Path:
        """Synthesize one sentence with the narrator reference. EN only."""
        del voice
        if lang not in SUPPORTED_LANGS:
            raise TTSSynthesisError("luxtts", f"unsupported language: {lang}")
        if self._lux is None:
            try:
                self._lux = self._cls("YatharthS/LuxTTS", device="mps")
            except (ImportError, RuntimeError, OSError) as exc:
                raise TTSSynthesisError("luxtts", f"model load failed: {exc}") from exc
        try:
            sf = importlib.import_module("soundfile")
            encoded = self._lux.encode_prompt(str(self._ref))
            audio = self._lux.generate_speech(
                text, encoded, num_steps=self._cfg.luxtts.num_steps, speed=speed
            )
            sf.write(str(out_path), audio.numpy().squeeze(), _SAMPLE_RATE)  # type: ignore[union-attr]
        except (RuntimeError, OSError, ValueError) as exc:
            raise TTSSynthesisError("luxtts", str(exc)[:300]) from exc
        return out_path
