"""Piper adapter: external binary per process, no code linkage (PLAN.md 7.2)."""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING, Final

from .base import TTSSynthesisError, TTSUnavailableError

if TYPE_CHECKING:
    from pathlib import Path

    from ..config import TTSConfig

_STDERR_TAIL: Final = 300
_TIMEOUT_S: Final = 120

# Piper model files are named like `de_DE-thorsten-high.onnx`; the stem's
# locale prefix decides which engine language a model serves.
PIPER_LOCALES: Final[dict[str, tuple[str, ...]]] = {
    "en": ("en_US", "en_GB"),
    "de": ("de_DE",),
}


class PiperEngine:
    """Invokes the piper binary; voices are *.onnx model stems in the model dir."""

    name: str = "piper"

    def __init__(self, cfg: TTSConfig) -> None:
        """Store config only; availability is checked via `is_available`."""
        self._cfg = cfg

    def is_available(self) -> bool:
        """Return True when the configured piper binary can be found on PATH."""
        return shutil.which(self._cfg.piper_path) is not None

    def voices(self, lang: str) -> list[str]:
        """Model stems whose locale prefix serves `lang`, sorted by name."""
        model_dir = self._cfg.piper_model_dir
        if model_dir is None:
            return []
        locales = PIPER_LOCALES.get(lang, ())
        stems = [m.stem for m in model_dir.glob("*.onnx") if m.stem.split("-", 1)[0] in locales]
        return sorted(stems)

    def synthesize(
        self, text: str, *, voice: str, lang: str, speed: float, out_path: Path
    ) -> Path:
        """Run piper with the resolved voice model; text on stdin."""
        _ = lang  # protocol conformance: the voice model's locale decides pronunciation
        model = self._resolve_model(voice)
        cmd = [self._cfg.piper_path, "--model", str(model), "--output_file", str(out_path)]
        if speed != 1.0:
            # piper's length_scale is the inverse of reading speed.
            cmd += ["--length-scale", f"{1.0 / speed:.6f}"]
        try:
            result = subprocess.run(
                cmd,
                input=f"{text}\n",
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_S,
                check=False,
            )
        except FileNotFoundError:
            raise TTSUnavailableError("piper") from None
        except subprocess.TimeoutExpired:
            raise TTSSynthesisError("piper", f"timed out after {_TIMEOUT_S}s") from None
        if result.returncode != 0:
            raise TTSSynthesisError("piper", (result.stderr or "").strip()[-_STDERR_TAIL:])
        return out_path

    def _resolve_model(self, voice: str) -> Path:
        model_dir = self._cfg.piper_model_dir
        if model_dir is None:
            raise TTSUnavailableError("piper")
        model = model_dir / f"{voice}.onnx"
        if not model.is_file():
            raise TTSUnavailableError("piper")
        return model
