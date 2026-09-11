"""Candidate selection: which books need which audio variant (PLAN.md 5.3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .client import CalibreBook

TEXT_SOURCE_ORDER: tuple[str, ...] = ("EPUB", "AZW3", "MOBI", "TXT", "PDF")
SKIP_STATUSES: frozenset[str] = frozenset(
    {"skipped_drm", "skipped_no_text", "skipped_manual"}
)


@dataclass(frozen=True, slots=True)
class Candidate:
    """A book selected for processing, with what it still needs."""

    book: CalibreBook
    needs_audiobook: bool
    needs_summary: bool
    source_format: str
    pdf_only: bool


def _source_format(formats: Sequence[str]) -> str | None:
    for preferred in TEXT_SOURCE_ORDER:
        if preferred in formats:
            return preferred
    return None


def select_candidates(books: Sequence[CalibreBook], *, restrict_tag: str) -> list[Candidate]:
    """Pure selection logic; no I/O. Ordering is preserved from input."""
    candidates: list[Candidate] = []
    for book in books:
        formats_upper = tuple(f.upper() for f in book.formats)
        source = _source_format(formats_upper)
        if source is None:
            continue
        if restrict_tag and restrict_tag not in book.tags:
            continue
        if book.audiobook_status in SKIP_STATUSES or book.summary_status in SKIP_STATUSES:
            continue
        has_audiobook = "M4B" in formats_upper or book.audiobook_status == "done"
        has_summary = "MP3" in formats_upper or book.summary_status == "done"
        needs_audiobook = not has_audiobook
        needs_summary = not has_summary
        if not needs_audiobook and not needs_summary:
            continue
        candidates.append(
            Candidate(
                book=book,
                needs_audiobook=needs_audiobook,
                needs_summary=needs_summary,
                source_format=source,
                pdf_only=source == "PDF" and not any(
                    f in formats_upper for f in TEXT_SOURCE_ORDER[:-1]
                ),
            )
        )
    return candidates
