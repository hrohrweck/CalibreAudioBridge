"""Extraction: OEB parsing, full pipeline via fakes, DRM/thin handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from calibreaudiobridge.calibre.client import CalibreBook, CalibreClient
from calibreaudiobridge.calibre.extract import Extractor, parse_oeb
from calibreaudiobridge.calibre.scanner import Candidate
from calibreaudiobridge.config import CalibreConfig
from calibreaudiobridge.errors import ExtractionError
from tests.conftest import FAKES_DIR, build_oeb, make_book


def _candidate(book: CalibreBook, source: str = "EPUB") -> Candidate:
    return Candidate(
        book=book, needs_audiobook=True, needs_summary=True, source_format=source, pdf_only=False
    )


def _book(book_id: int = 1, languages: tuple[str, ...] = ("en",)) -> CalibreBook:
    return CalibreBook(
        id=book_id,
        title="Test Book",
        authors=("A. Author",),
        formats=("EPUB",),
        languages=languages,
        tags=(),
        audiobook_status="",
        summary_status="",
    )


def _cfg(tmp_path: Path) -> CalibreConfig:
    return CalibreConfig(
        library_path=tmp_path / "lib",
        calibredb_path=str(FAKES_DIR / "calibredb"),
        ebook_convert_path=str(FAKES_DIR / "ebook-convert"),
    )


CHAPTERS: list[tuple[str, list[str]]] = [
    ("The Beginning", ["It was 3 km to the station in 1997.", "He had $5 and 12 apples."]),
    ("The Middle", ["She said: 'wait here'.", "The End is near."]),
]


class TestParseOeb_whenWellFormed:
    def test_chapters_titles_and_text(self, tmp_path: Path) -> None:
        oeb = build_oeb(tmp_path / "oeb", CHAPTERS)
        chapters = parse_oeb(oeb)
        assert [c.title for c in chapters] == ["The Beginning", "The Middle"]
        assert "It was 3 km to the station in 1997." in chapters[0].text
        assert chapters[0].index == 0
        assert chapters[1].index == 1

    def test_short_chapters_are_dropped(self, tmp_path: Path) -> None:
        oeb = build_oeb(tmp_path / "oeb", [*CHAPTERS, ("Footer", ["tiny"])])
        chapters = parse_oeb(oeb)
        assert [c.title for c in chapters] == ["The Beginning", "The Middle"]

    def test_h1_fallback_when_no_ncx(self, tmp_path: Path) -> None:
        oeb = build_oeb(tmp_path / "oeb", CHAPTERS)
        (tmp_path / "oeb" / "OEBPS" / "toc.ncx").unlink()
        chapters = parse_oeb(oeb)
        assert chapters[0].title == "The Beginning"


class TestExtractor_whenFullPipeline:
    def test_extracts_chapters_via_fakes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "source.epub"
        source.write_bytes(b"epub")
        monkeypatch.setenv("FAKE_CALIBRE_EXPORT_SRC", str(source))
        long_chapters = [
            ("Alpha", [" ".join(f"alpha{i}" for i in range(120))]),
            ("Beta", [" ".join(f"beta{i}" for i in range(120))]),
        ]
        monkeypatch.setenv(
            "FAKE_EBOOKCONVERT_OEB", str(build_oeb(tmp_path / "oebsrc", long_chapters))
        )
        books = tmp_path / "books.json"
        books.write_text("[]")
        monkeypatch.setenv("FAKE_CALIBRE_BOOKS", str(books))
        client = CalibreClient(_cfg(tmp_path))
        extractor = Extractor(_cfg(tmp_path), tmp_path / "work")
        result = extractor.extract(client, _candidate(_book()))
        assert [c.title for c in result.chapters] == ["Alpha", "Beta"]
        assert result.language == "en"
        assert result.total_words >= 200

    def test_txt_fallback_single_chapter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "source.txt"
        source.write_text("txt")
        monkeypatch.setenv("FAKE_CALIBRE_EXPORT_SRC", str(source))
        long_text = "\n\n".join(" ".join(f"w{i}" for i in range(60)) for _ in range(5))
        monkeypatch.setenv("FAKE_EBOOKCONVERT_TXT", long_text)
        books = tmp_path / "books.json"
        books.write_text("[]")
        monkeypatch.setenv("FAKE_CALIBRE_BOOKS", str(books))
        client = CalibreClient(_cfg(tmp_path))
        extractor = Extractor(_cfg(tmp_path), tmp_path / "work")
        result = extractor.extract(client, _candidate(_book(), "TXT"))
        assert len(result.chapters) == 1
        assert result.chapters[0].title == "Full Book"


class TestExtractor_whenFailing:
    def test_drm_raises_drm_reason(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = tmp_path / "source.mobi"
        source.write_bytes(b"mobi")
        monkeypatch.setenv("FAKE_CALIBRE_EXPORT_SRC", str(source))
        monkeypatch.setenv("FAKE_EBOOKCONVERT_DRM", "1")
        books = tmp_path / "books.json"
        books.write_text("[]")
        monkeypatch.setenv("FAKE_CALIBRE_BOOKS", str(books))
        client = CalibreClient(_cfg(tmp_path))
        extractor = Extractor(_cfg(tmp_path), tmp_path / "work")
        with pytest.raises(ExtractionError) as excinfo:
            extractor.extract(client, _candidate(_book()))
        assert excinfo.value.reason == "drm"

    def test_thin_text_raises_thin_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "source.txt"
        source.write_text("txt")
        monkeypatch.setenv("FAKE_CALIBRE_EXPORT_SRC", str(source))
        monkeypatch.setenv("FAKE_EBOOKCONVERT_TXT", "only a handful of words")
        books = tmp_path / "books.json"
        books.write_text("[]")
        monkeypatch.setenv("FAKE_CALIBRE_BOOKS", str(books))
        client = CalibreClient(_cfg(tmp_path))
        extractor = Extractor(_cfg(tmp_path), tmp_path / "work")
        with pytest.raises(ExtractionError) as excinfo:
            extractor.extract(client, _candidate(_book(), "TXT"))
        assert excinfo.value.reason == "thin_text"


def test_make_book_fixture_shape() -> None:
    row = make_book(1, formats="EPUB")
    assert row["formats"] == "EPUB"
    assert row["#cab_audiobook_status"] == ""
