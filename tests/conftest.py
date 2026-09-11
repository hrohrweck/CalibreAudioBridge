"""Shared fixtures: fake binaries, book rows, OEB builder, config factory."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from calibreaudiobridge.config import CalibreConfig

FAKES_DIR = Path(__file__).parent / "fakes"

_FAKE_ENV_VARS = (
    "FAKE_CALIBRE_LOG",
    "FAKE_CALIBRE_BOOKS",
    "FAKE_CALIBRE_LOCK",
    "FAKE_CALIBRE_EXPORT_SRC",
    "FAKE_CUSTOM_COLUMNS",
    "FAKE_SERVER_DOWN",
    "FAKE_EBOOKCONVERT_OEB",
    "FAKE_EBOOKCONVERT_TXT",
    "FAKE_EBOOKCONVERT_DRM",
    "FAKE_EBOOKCONVERT_FAIL",
)


@pytest.fixture(autouse=True)
def clean_fake_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts with a clean fake-binary environment."""
    for var in _FAKE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def make_book(
    book_id: int,
    *,
    title: str = "Book",
    formats: str = "EPUB",
    languages: str = "eng",
    tags: tuple[str, ...] = (),
    authors: tuple[str, ...] = ("A. Author",),
    audiobook_status: str = "",
    summary_status: str = "",
) -> dict[str, Any]:
    """One calibredb --for-machine row."""
    return {
        "id": book_id,
        "title": title,
        "authors": list(authors),
        "formats": formats,
        "languages": languages,
        "tags": list(tags),
        "#cab_audiobook_status": audiobook_status,
        "#cab_summary_status": summary_status,
    }


@pytest.fixture
def book_row() -> Callable[..., dict[str, Any]]:
    return make_book


@pytest.fixture
def fake_calibre(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., CalibreConfig]:
    """Factory wiring env for the fake calibredb; returns a matching config."""

    def setup(
        books: list[dict[str, Any]] | None = None,
        *,
        lock: bool = False,
        custom_columns: str = "missing",
        server_url: str | None = None,
    ) -> CalibreConfig:
        books_path = tmp_path / "books.json"
        books_path.write_text(json.dumps(books or []))
        monkeypatch.setenv("FAKE_CALIBRE_BOOKS", str(books_path))
        monkeypatch.setenv("FAKE_CUSTOM_COLUMNS", custom_columns)
        if lock:
            monkeypatch.setenv("FAKE_CALIBRE_LOCK", "1")
        return CalibreConfig(
            library_path=tmp_path / "lib",
            server_url=server_url,
            calibredb_path=str(FAKES_DIR / "calibredb"),
            ebook_convert_path=str(FAKES_DIR / "ebook-convert"),
        )

    return setup


def build_oeb(root: Path, chapters: list[tuple[str, list[str]]]) -> Path:
    """Write a minimal OEB folder: content.opf + toc.ncx + chapter xhtml files."""
    oebps = root / "OEBPS"
    oebps.mkdir(parents=True, exist_ok=True)
    manifest, spine, navpoints = [], [], []
    for i, (title, paragraphs) in enumerate(chapters, start=1):
        fname = f"ch{i}.xhtml"
        manifest.append(f'<item id="c{i}" href="{fname}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{i}"/>')
        navpoints.append(
            f'<navPoint id="n{i}"><navLabel><text>{title}</text></navLabel>'
            f'<content src="{fname}"/></navPoint>'
        )
        body = "".join(f"<p>{p}</p>" for p in paragraphs)
        (oebps / fname).write_text(
            '<?xml version="1.0"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>'
            f"{title}</title></head><body><h1>{title}</h1>{body}</body></html>",
            encoding="utf-8",
        )
    (oebps / "content.opf").write_text(
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        f'<metadata/><manifest>{"".join(manifest)}</manifest>'
        f'<spine>{"".join(spine)}</spine></package>',
        encoding="utf-8",
    )
    (oebps / "toc.ncx").write_text(
        '<?xml version="1.0"?>'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        f'<head/><navMap>{"".join(navpoints)}</navMap></ncx>',
        encoding="utf-8",
    )
    return root


@pytest.fixture
def oeb_builder() -> Callable[[Path, list[tuple[str, list[str]]]], Path]:
    return build_oeb


def mock_llm_http(response_text: str | None = None) -> httpx.Client:
    """httpx client against an in-process OpenAI-compatible mock.

    Echoes the last user message (drift-guard-friendly) or returns
    `response_text` when given.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        messages = payload.get("messages", [])
        content = response_text
        if content is None:
            content = messages[-1]["content"] if messages else ""
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mock/v1")
