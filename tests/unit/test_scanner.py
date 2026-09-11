"""Candidate selection matrix (PLAN.md 5.3)."""

from __future__ import annotations

from calibreaudiobridge.calibre.client import CalibreBook
from calibreaudiobridge.calibre.scanner import select_candidates


def book(
    book_id: int = 1,
    *,
    formats: tuple[str, ...] = ("EPUB",),
    audiobook_status: str = "",
    summary_status: str = "",
    tags: tuple[str, ...] = (),
) -> CalibreBook:
    return CalibreBook(
        id=book_id,
        title=f"Book {book_id}",
        authors=("A. Author",),
        formats=formats,
        languages=("en",),
        tags=tags,
        audiobook_status=audiobook_status,
        summary_status=summary_status,
    )


class TestSelectCandidates_whenEligible:
    def test_epub_only_book_needs_both(self) -> None:
        (candidate,) = select_candidates([book(1)], restrict_tag="")
        assert candidate.needs_audiobook
        assert candidate.needs_summary
        assert candidate.source_format == "EPUB"

    def test_existing_m4b_needs_only_summary(self) -> None:
        (candidate,) = select_candidates([book(1, formats=("EPUB", "M4B"))], restrict_tag="")
        assert not candidate.needs_audiobook
        assert candidate.needs_summary

    def test_done_status_counts_as_done_even_without_file(self) -> None:
        (candidate,) = select_candidates(
            [book(1, audiobook_status="done")], restrict_tag=""
        )
        assert not candidate.needs_audiobook
        assert candidate.needs_summary


class TestSelectCandidates_whenExcluded:
    def test_complete_book_is_skipped(self) -> None:
        assert select_candidates(
            [book(1, formats=("EPUB", "M4B", "MP3"))], restrict_tag=""
        ) == []

    def test_both_done_statuses_skip(self) -> None:
        assert select_candidates(
            [book(1, audiobook_status="done", summary_status="done")], restrict_tag=""
        ) == []

    def test_no_text_format_is_skipped(self) -> None:
        assert select_candidates([book(1, formats=("M4B",))], restrict_tag="") == []

    def test_any_skip_status_blocks_book(self) -> None:
        assert (
            select_candidates(
                [book(1, audiobook_status="skipped_drm", summary_status="")], restrict_tag=""
            )
            == []
        )


class TestSelectCandidates_whenConstrained:
    def test_restrict_tag_filters(self) -> None:
        candidates = select_candidates(
            [book(1), book(2, tags=("tts",))], restrict_tag="tts"
        )
        assert [c.book.id for c in candidates] == [2]

    def test_pdf_only_flagged(self) -> None:
        (candidate,) = select_candidates([book(1, formats=("PDF",))], restrict_tag="")
        assert candidate.pdf_only
        assert candidate.source_format == "PDF"

    def test_epub_preferred_over_pdf(self) -> None:
        (candidate,) = select_candidates([book(1, formats=("PDF", "EPUB"))], restrict_tag="")
        assert candidate.source_format == "EPUB"
        assert not candidate.pdf_only

    def test_txt_source_used_when_only_option(self) -> None:
        (candidate,) = select_candidates([book(1, formats=("TXT",))], restrict_tag="")
        assert candidate.source_format == "TXT"
