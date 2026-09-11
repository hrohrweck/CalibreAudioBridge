"""Typed errors. Exit-code mapping lives in the CLI layer."""

from __future__ import annotations

from enum import StrEnum
from typing import assert_never


class CabError(Exception):
    """Base class for all CalibreAudioBridge errors."""


class ConfigError(CabError):
    """Configuration is missing or invalid. Exit code 2."""

    detail: str

    def __init__(self, detail: str) -> None:
        super().__init__(f"configuration error: {detail}")
        self.detail = detail


class LockHeldError(CabError):
    """Another cab process holds the instance lock. Exit code 2."""

    lock_path: str

    def __init__(self, lock_path: str) -> None:
        super().__init__(f"another cab instance is running (lock: {lock_path})")
        self.lock_path = lock_path


class CalibreEnvError(CabError):
    """calibredb/ebook-convert missing or unusable. Exit code 2."""

    detail: str

    def __init__(self, detail: str) -> None:
        super().__init__(f"calibre environment error: {detail}")
        self.detail = detail


class CalibreBlockedError(CabError):
    """Library is locked by a running calibre and no server is configured. Exit code 3."""

    library_path: str

    def __init__(self, library_path: str) -> None:
        super().__init__(
            "calibre GUI/server is running and holds the library lock; "
            "start calibre's Content Server or close calibre "
            f"(library: {library_path})"
        )
        self.library_path = library_path


class ExtractionReason(StrEnum):
    """Reason tags for extraction failures."""

    DRM = "drm"
    CONVERT_FAILED = "convert_failed"
    THIN_TEXT = "thin_text"


class ExtractionError(CabError):
    """Text extraction failed for a book."""

    book_id: int
    reason: ExtractionReason
    detail: str

    def __init__(self, book_id: int, reason: ExtractionReason, detail: str) -> None:
        message = _message_for(reason, book_id, detail)
        super().__init__(message)
        self.book_id = book_id
        self.reason = reason
        self.detail = detail


def _message_for(reason: ExtractionReason, book_id: int, detail: str) -> str:
    match reason:
        case ExtractionReason.DRM:
            return f"book {book_id}: source has DRM, conversion refused ({detail})"
        case ExtractionReason.CONVERT_FAILED:
            return f"book {book_id}: ebook-convert failed ({detail})"
        case ExtractionReason.THIN_TEXT:
            return f"book {book_id}: extracted text is too thin to narrate ({detail})"
        case unreachable:
            assert_never(unreachable)
