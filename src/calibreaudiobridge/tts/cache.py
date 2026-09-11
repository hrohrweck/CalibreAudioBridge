"""Content-addressed TTS cache: sha256(engine, voice, speed, lang, text) -> wav (PLAN.md 7.3)."""

from __future__ import annotations

import hashlib
import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_FIELD_SEP = "\x00"


class TTSCache:
    """Sharded wav store under one root; `get` only returns paths that exist."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def key(self, *, engine: str, voice: str, speed: float, lang: str, text: str) -> str:
        """Stable sha256 over every input that changes the produced audio."""
        blob = _FIELD_SEP.join((engine, voice, repr(speed), lang, text))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Path | None:
        """Return the cached wav path for `key`, or None when nothing is stored."""
        path = self._path_for(key)
        return path if path.is_file() else None

    def put(self, key: str, wav_path: Path) -> Path:
        """Copy `wav_path` into the sharded store and return the stored path."""
        dest = self._path_for(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wav_path, dest)
        return dest

    def _path_for(self, key: str) -> Path:
        return self._root / key[:2] / f"{key}.wav"
