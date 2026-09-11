"""Audiobook listenability preprocessing: deterministic rules + optional LLM pass.

Stage 1 always runs and is pure. Stage 2 asks the LLM for spoken-form edits only
and falls back to the Stage-1 text when the output drifts or the chain is down.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from num2words import num2words

from ..calibre.extract import Chapter
from ..langs import normalize_language
from .chunking import chunk_chapters
from .client import LLMExhaustedError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..config import PreprocessConfig
    from .chunking import TokenCounter
    from .client import LLMClient, Message

_FOOTNOTE_RE: Final = re.compile(r"\[\d+\]|\[footnote[^\]]*\]", re.IGNORECASE)
_URL_RE: Final = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_NUMBER_RE: Final = re.compile(r"\d+(?:[.,]\d+)?")
_LINK_EN: Final = "(Link)"
_LINK_DE: Final = "(link)"
_MAX_OUTPUT_TOKENS: Final = 4096

_EDIT_INSTRUCTION: Final = (
    "You are a copy editor preparing a book for text-to-speech narration. "
    "Return the SAME text with only listenability edits: expand abbreviations, "
    "dates, currencies and numbers to natural spoken form; fix OCR artifacts and "
    "hyphenation; remove footnote, figure and sidenote references. Keep the "
    "language, meaning and length; change nothing else. Output only the edited text."
)
_STRICT_EDIT_INSTRUCTION: Final = (
    _EDIT_INSTRUCTION
    + " Copy the input almost verbatim: do not rephrase, summarize, add or remove content."
)


@dataclass(frozen=True, slots=True)
class PreprocessedChapter:
    """One chapter after Stage 1 and, when applied, the Stage-2 LLM pass."""

    text: str
    used_llm: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Editor:
    """Bundle of the Stage-2 dependencies (keeps call signatures small)."""

    lang: str
    counter: TokenCounter
    client: LLMClient
    cfg: PreprocessConfig


def preprocess(
    chapters: Sequence[Chapter],
    *,
    language: str,
    counter: TokenCounter,
    client: LLMClient,
    cfg: PreprocessConfig,
) -> list[PreprocessedChapter]:
    """Run Stage 1 always; add the LLM edit pass when enabled and available."""
    lang = normalize_language(language)
    use_llm = cfg.enabled and client.has_models
    editor = _Editor(lang=lang, counter=counter, client=client, cfg=cfg)
    results: list[PreprocessedChapter] = []
    for chapter in chapters:
        stage1 = _stage1(chapter.text, lang)
        if not use_llm:
            results.append(PreprocessedChapter(text=stage1, used_llm=False, warnings=()))
            continue
        results.append(_stage2(chapter, stage1, editor))
    return results


def _stage2(chapter: Chapter, stage1: str, editor: _Editor) -> PreprocessedChapter:
    budget = editor.client.input_budget_tokens
    probe = Chapter(index=chapter.index, title=chapter.title, text=stage1)
    units = chunk_chapters([probe], budget, editor.counter)
    if not units:
        return PreprocessedChapter(text=stage1, used_llm=False, warnings=())
    edited: list[str] = []
    warnings: list[str] = []
    used = False
    for unit in units:
        text, unit_used, unit_warnings = _edit_unit(unit.text, editor)
        edited.append(text)
        used = used or unit_used
        warnings.extend(unit_warnings)
    return PreprocessedChapter(
        text="\n\n".join(edited),
        used_llm=used,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _edit_unit(text: str, editor: _Editor) -> tuple[str, bool, tuple[str, ...]]:
    if not text.strip():
        return text, False, ()
    input_tokens = editor.counter.count(text)
    max_out = min(input_tokens * 2, _MAX_OUTPUT_TOKENS)
    try:
        completion = editor.client.complete(
            _edit_messages(text, editor.lang), max_output_tokens=max_out
        )
    except LLMExhaustedError:
        return text, False, ("llm_unavailable_fallback",)
    if _within_drift(completion.text, text, editor.cfg):
        return completion.text, True, ()
    try:
        retry = editor.client.complete(
            _strict_edit_messages(text, editor.lang), max_output_tokens=max_out
        )
    except LLMExhaustedError:
        return text, False, ("llm_unavailable_fallback",)
    if _within_drift(retry.text, text, editor.cfg):
        return retry.text, True, ()
    return text, False, ("llm_drift_fallback",)


def _within_drift(output: str, source: str, cfg: PreprocessConfig) -> bool:
    if not source:
        return not output
    ratio = len(output) / len(source)
    return cfg.drift_min_ratio <= ratio <= cfg.drift_max_ratio


def _stage1(text: str, lang: str) -> str:
    text = _FOOTNOTE_RE.sub("", text)
    text = _URL_RE.sub(_LINK_EN if lang == "en" else _LINK_DE, text)
    return _NUMBER_RE.sub(lambda match: _number_words(match.group(0), lang), text)


def _number_words(raw: str, lang: str) -> str:
    value = _parse_number(raw, lang)
    if value is None:
        return raw
    for code in dict.fromkeys((lang, "en")):
        try:
            return num2words(value, lang=code)
        except (ValueError, NotImplementedError, OverflowError):
            continue
    return raw


def _parse_number(raw: str, lang: str) -> int | float | None:
    try:
        if "." in raw or "," in raw:
            normalized = raw.replace(",", ".") if lang == "de" else raw.replace(",", "")
            value = float(normalized)
            return int(value) if value.is_integer() else value
        return int(raw)
    except ValueError:
        return None


def _edit_messages(text: str, lang: str) -> list[Message]:
    return [
        {"role": "system", "content": _EDIT_INSTRUCTION},
        {"role": "user", "content": f"Language: {lang}\n\n{text}"},
    ]


def _strict_edit_messages(text: str, lang: str) -> list[Message]:
    return [
        {"role": "system", "content": _STRICT_EDIT_INSTRUCTION},
        {"role": "user", "content": f"Language: {lang}\n\n{text}"},
    ]
