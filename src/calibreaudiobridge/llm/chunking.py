"""Chapter -> paragraph -> sentence chunking sized to a token budget (PLAN.md 6.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

    import tiktoken

    from ..calibre.extract import Chapter

_ENCODING_NAME: Final = "cl100k_base"
_CHARS_PER_TOKEN: Final = 4
_PARAGRAPH_RE: Final = re.compile(r"\n\s*\n")

_SENTENCE_END: Final = frozenset(".!?…")
_TRAILING_CLOSERS: Final = frozenset("\"')]}»”")
_DOUBLE_QUOTES: Final = frozenset('"“”„«»')
_OPEN_BRACKETS: Final = frozenset("([{")
_CLOSE_BRACKETS: Final = frozenset(")]}")

# en + de abbreviations whose trailing period must not end a sentence.
_ABBREVIATIONS: Final = frozenset(
    {
        "dr", "mr", "mrs", "ms", "st", "prof", "sr", "jr", "vs", "etc", "e.g", "i.e",
        "nr", "z.b", "usw", "bzw", "ca", "vgl", "abs", "bd", "bspw", "evtl", "inkl",
        "max", "min", "mio", "mrd", "sog", "u.a", "d.h", "o.ä", "ggf", "zzgl",
    }
)


@lru_cache(maxsize=1)
def _load_encoding() -> tiktoken.Encoding | None:
    try:
        import tiktoken  # noqa: PLC0415 - lazy so a missing tiktoken degrades to chars/4
    except ImportError:
        return None
    try:
        return tiktoken.get_encoding(_ENCODING_NAME)
    except Exception:  # noqa: BLE001 - any init/download failure falls back to chars/4
        return None


class TokenCounter:
    """Token counting via tiktoken cl100k_base with a chars/4 fallback."""

    def __init__(self) -> None:
        self._encoding: tiktoken.Encoding | None = None
        self._resolved = False

    def count(self, text: str) -> int:
        """Approximate the cl100k_base token count of ``text``."""
        if not self._resolved:
            self._encoding = _load_encoding()
            self._resolved = True
        if self._encoding is None:
            return len(text) // _CHARS_PER_TOKEN
        return len(self._encoding.encode(text, disallowed_special=()))


@dataclass(frozen=True, slots=True)
class Chunk:
    """One unit of text ready for an LLM call."""

    index: int
    chapter_index: int
    chapter_title: str
    text: str


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences, keeping punctuation and respecting nesting.

    Abbreviations (en/de) and decimals do not end a sentence; neither does
    punctuation inside brackets or double quotes.
    """
    sentences: list[str] = []
    start = 0
    depth = 0
    in_quote = False
    index = 0
    while index < len(text):
        char = text[index]
        if char in _SENTENCE_END:
            end = _sentence_end(text, index)
            end_depth, end_quote = _state_after(text, index + 1, end, depth, in_quote=in_quote)
            if end_depth == 0 and not end_quote and _ends_sentence(text, index, end):
                sentence = text[start:end].strip()
                if sentence:
                    sentences.append(sentence)
                start = end
                depth, in_quote = end_depth, end_quote
                index = end
                continue
        depth, in_quote = _scan_state(char, depth, in_quote=in_quote)
        index += 1
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def _state_after(
    text: str, start: int, end: int, depth: int, *, in_quote: bool
) -> tuple[int, bool]:
    for position in range(start, end):
        depth, in_quote = _scan_state(text[position], depth, in_quote=in_quote)
    return depth, in_quote


def _scan_state(char: str, depth: int, *, in_quote: bool) -> tuple[int, bool]:
    if char in _OPEN_BRACKETS:
        return depth + 1, in_quote
    if char in _CLOSE_BRACKETS:
        return max(0, depth - 1), in_quote
    if char in _DOUBLE_QUOTES:
        return depth, not in_quote
    return depth, in_quote


def _sentence_end(text: str, index: int) -> int:
    end = index + 1
    while end < len(text) and text[end] in _SENTENCE_END:
        end += 1
    while end < len(text) and text[end] in _TRAILING_CLOSERS:
        end += 1
    return end


def _ends_sentence(text: str, punct_index: int, after_index: int) -> bool:
    if text[punct_index] == "." and _is_abbreviation(text, punct_index):
        return False
    if after_index >= len(text):
        return True
    if not text[after_index].isspace():
        return False
    following = text[after_index:].lstrip()
    if not following:
        return True
    head = following[0]
    return head.isupper() or head.isdigit() or head in _OPEN_BRACKETS or head in _DOUBLE_QUOTES


def _is_abbreviation(text: str, punct_index: int) -> bool:
    cursor = punct_index
    while cursor > 0 and (text[cursor - 1].isalnum() or text[cursor - 1] in ".&"):
        cursor -= 1
    word = text[cursor:punct_index].lower().strip(".")
    if not word:
        return False
    if word in _ABBREVIATIONS:
        return True
    return len(word) == 1 and word.isalpha()


def chunk_chapters(
    chapters: Sequence[Chapter], budget_tokens: int, counter: TokenCounter
) -> list[Chunk]:
    """Pack chapters into budget-sized chunks without splitting sentences."""
    chunks: list[Chunk] = []
    for chapter in chapters:
        for text in _split_chapter(chapter.text, budget_tokens, counter):
            chunks.append(
                Chunk(
                    index=len(chunks),
                    chapter_index=chapter.index,
                    chapter_title=chapter.title,
                    text=text,
                )
            )
    return chunks


def _split_chapter(text: str, budget_tokens: int, counter: TokenCounter) -> list[str]:
    if not text.strip():
        return []
    if counter.count(text) <= budget_tokens:
        return [text]
    units: list[str] = []
    pending: list[str] = []
    pending_tokens = 0
    separator_tokens = counter.count("\n\n")
    for paragraph in _paragraphs(text):
        tokens = counter.count(paragraph)
        if tokens > budget_tokens:
            if pending:
                units.append("\n\n".join(pending))
            pending = []
            pending_tokens = 0
            units.extend(_pack_sentences(paragraph, budget_tokens, counter))
            continue
        if pending and pending_tokens + separator_tokens + tokens > budget_tokens:
            units.append("\n\n".join(pending))
            pending = []
            pending_tokens = 0
        pending_tokens = pending_tokens + separator_tokens + tokens if pending else tokens
        pending.append(paragraph)
    if pending:
        units.append("\n\n".join(pending))
    return units


def _pack_sentences(text: str, budget_tokens: int, counter: TokenCounter) -> list[str]:
    units: list[str] = []
    pending: list[str] = []
    pending_tokens = 0
    separator_tokens = counter.count(" ")
    for sentence in split_sentences(text):
        tokens = counter.count(sentence)
        if tokens > budget_tokens:
            if pending:
                units.append(" ".join(pending))
            pending = []
            pending_tokens = 0
            units.append(sentence)
            continue
        if pending and pending_tokens + separator_tokens + tokens > budget_tokens:
            units.append(" ".join(pending))
            pending = []
            pending_tokens = 0
        pending_tokens = pending_tokens + separator_tokens + tokens if pending else tokens
        pending.append(sentence)
    if pending:
        units.append(" ".join(pending))
    return units


def _paragraphs(text: str) -> list[str]:
    return [paragraph.strip() for paragraph in _PARAGRAPH_RE.split(text) if paragraph.strip()]
