"""Text + chapter extraction via ebook-convert's OEB output (PLAN.md 6.1)."""

from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Final, override
from urllib.parse import unquote

from ..errors import ExtractionError, ExtractionReason

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ..config import CalibreConfig
    from .client import CalibreClient
    from .scanner import Candidate

_NS_OPF: Final = {"opf": "http://www.idpf.org/2007/opf"}
_NS_NCX: Final = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}
_HTML_SUFFIXES: Final = frozenset({".html", ".xhtml", ".htm"})
_MIN_CHAPTER_WORDS: Final = 10
_MIN_BOOK_WORDS: Final = 200
_CONVERT_TIMEOUT_S: Final = 600


@dataclass(frozen=True, slots=True)
class Chapter:
    """One narratable chapter: title plus cleaned prose."""

    index: int
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class ExtractedBook:
    """Extraction result for one book."""

    book_id: int
    title: str
    language: str
    chapters: tuple[Chapter, ...]
    total_words: int
    warnings: tuple[str, ...]


class _HTMLTextParser(HTMLParser):
    """Collect visible text; remember the first h1 as a title candidate."""

    _SKIP_TAGS: Final[frozenset[str]] = frozenset({"script", "style", "head"})
    _BLOCK_TAGS: Final[frozenset[str]] = frozenset(
        {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "br",
         "section", "article", "blockquote"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._chunks: list[str] = []
        self._h1_parts: list[str] | None = None
        self.h1: str | None = None

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        if tag == "h1":
            self._h1_parts = []
        if tag in self._BLOCK_TAGS:
            self._chunks.append("\n\n")

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "h1" and self._h1_parts is not None:
            joined = " ".join(self._h1_parts).strip()
            self.h1 = joined or None
            self._h1_parts = None

    @override
    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        self._chunks.append(data)
        if self._h1_parts is not None:
            self._h1_parts.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", raw)]
        return "\n\n".join(p for p in paragraphs if p)


def _toc_titles(oeb_dir: Path) -> dict[str, str]:
    titles: dict[str, str] = {}
    for ncx in sorted(oeb_dir.rglob("*.ncx")):
        try:
            root = ET.parse(ncx).getroot()
        except ET.ParseError:
            continue
        for point in root.findall(".//ncx:navPoint", _NS_NCX):
            label = point.find("ncx:navLabel/ncx:text", _NS_NCX)
            content = point.find("ncx:content", _NS_NCX)
            title = (label.text or "").strip() if label is not None else ""
            if content is None or not title:
                continue
            src = (content.get("src") or "").split("#")[0].split("/")[-1]
            if src:
                titles[unquote(src)] = title
    return titles


def parse_oeb(oeb_dir: Path) -> list[Chapter]:
    """Parse an ebook-convert OEB folder into chapters (spine order)."""
    opf_files = sorted(oeb_dir.rglob("content.opf"))
    if not opf_files:
        return []
    opf = opf_files[0]
    try:
        root = ET.parse(opf).getroot()
    except ET.ParseError:
        return []
    base = opf.parent
    manifest: dict[str, str] = {}
    for item in root.findall("opf:manifest/opf:item", _NS_OPF):
        item_id, href = item.get("id"), item.get("href")
        if item_id and href:
            manifest[item_id] = href
    spine = [
        idref
        for itemref in root.findall("opf:spine/opf:itemref", _NS_OPF)
        if (idref := itemref.get("idref")) in manifest
    ]
    titles = _toc_titles(oeb_dir)
    chapters: list[Chapter] = []
    for idref in spine:
        path = (base / unquote(manifest[idref].split("/")[-1])).resolve()
        if path.suffix.lower() not in _HTML_SUFFIXES or not path.is_file():
            continue
        parser = _HTMLTextParser()
        parser.feed(path.read_text(encoding="utf-8", errors="replace"))
        text = parser.text()
        if len(text.split()) < _MIN_CHAPTER_WORDS:
            continue
        title = titles.get(path.name) or parser.h1 or f"Chapter {len(chapters) + 1}"
        chapters.append(Chapter(index=len(chapters), title=title, text=text))
    return chapters


def _words(chapters: Sequence[Chapter]) -> int:
    return sum(len(c.text.split()) for c in chapters)


class Extractor:
    """Export a book's source file and extract chapters via ebook-convert."""

    def __init__(self, cfg: CalibreConfig, work_dir: Path) -> None:
        self._cfg = cfg
        self._work_dir = work_dir

    def extract(self, client: CalibreClient, candidate: Candidate) -> ExtractedBook:
        """Export, convert to OEB, parse chapters; raise ExtractionError on failure."""
        book = candidate.book
        staging = self._work_dir / "extract" / str(book.id)
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        source = client.export(book.id, candidate.source_format, staging / "src")

        chapters = self._oeb_chapters(book.id, source, staging / "oeb")
        if not chapters:
            chapters = self._txt_chapters(book.id, source, staging / "book.txt")
        total = _words(chapters)
        if total < _MIN_BOOK_WORDS:
            raise ExtractionError(
                book.id,
                ExtractionReason.THIN_TEXT,
                f"only {total} words extracted from {candidate.source_format}",
            )
        warnings = []
        if candidate.pdf_only:
            warnings.append("pdf_source")
        language = book.languages[0] if book.languages else "en"
        return ExtractedBook(
            book_id=book.id,
            title=book.title,
            language=language,
            chapters=tuple(chapters),
            total_words=total,
            warnings=tuple(warnings),
        )

    def _oeb_chapters(self, book_id: int, source: Path, oeb_dir: Path) -> list[Chapter]:
        self._convert(book_id, source, oeb_dir)
        return parse_oeb(oeb_dir)

    def _txt_chapters(self, book_id: int, source: Path, txt_path: Path) -> list[Chapter]:
        self._convert(book_id, source, txt_path)
        text = txt_path.read_text(encoding="utf-8", errors="replace").strip()
        paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
        return [Chapter(index=0, title="Full Book", text="\n\n".join(p for p in paragraphs if p))]

    def _convert(self, book_id: int, source: Path, target: Path) -> None:
        cmd = [self._cfg.ebook_convert_path, str(source), str(target)]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=_CONVERT_TIMEOUT_S, check=False
            )
        except FileNotFoundError:
            raise ExtractionError(
                book_id,
                ExtractionReason.CONVERT_FAILED,
                f"ebook-convert not found: {self._cfg.ebook_convert_path}",
            ) from None
        except subprocess.TimeoutExpired:
            raise ExtractionError(
                book_id, ExtractionReason.CONVERT_FAILED, "ebook-convert timed out"
            ) from None
        if result.returncode != 0:
            stderr = result.stderr.strip()[:300]
            reason = (
                ExtractionReason.DRM
                if "drm" in stderr.lower()
                else ExtractionReason.CONVERT_FAILED
            )
            raise ExtractionError(book_id, reason, stderr)
