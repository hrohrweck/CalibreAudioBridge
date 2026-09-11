"""Map-reduce summarization to a target listening length (PLAN.md 6.3).

Map each chunk to a short summary with a rolling story-so-far context, merge the
summaries in token-budgeted batches, then produce the target-length summary in
the book's language.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from ..errors import ConfigError
from ..langs import normalize_language

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..config import SummaryConfig
    from .chunking import Chunk, TokenCounter
    from .client import LLMClient, Message

_SECTION_BUDGET_TOKENS: Final = 3000
_SECTION_OUTPUT_TOKENS: Final = 1024
_MAP_RATIO: Final = 0.03
_MAP_MIN_WORDS: Final = 30
_MIN_OUTPUT_TOKENS: Final = 64
_MAX_OUTPUT_TOKENS: Final = 8192
_TOKENS_PER_WORD: Final = 2


def summarize(
    chunks: Sequence[Chunk],
    *,
    language: str,
    client: LLMClient,
    cfg: SummaryConfig,
    counter: TokenCounter,
) -> str:
    """Summarize chunks into a target-length prose summary in ``language``."""
    if not client.has_models:
        raise ConfigError("summary needs at least one [[llm.models]] entry")
    if not chunks:
        return ""
    lang = normalize_language(language)
    target_words = cfg.target_minutes * cfg.words_per_minute
    mapped = _map_chunks(chunks, lang, client)
    reduced = _reduce(mapped, lang, client, counter)
    return _final(reduced, lang, client, target_words)


def _map_chunks(
    chunks: Sequence[Chunk], lang: str, client: LLMClient
) -> list[str]:
    summaries: list[str] = []
    story_so_far = ""
    for chunk in chunks:
        target_words = max(_MAP_MIN_WORDS, int(len(chunk.text.split()) * _MAP_RATIO))
        completion = client.complete(
            _map_messages(chunk.text, story_so_far, target_words, lang),
            max_output_tokens=_output_tokens(target_words),
        )
        summary = completion.text.strip()
        summaries.append(summary)
        story_so_far = summary
    return summaries


def _reduce(
    summaries: list[str], lang: str, client: LLMClient, counter: TokenCounter
) -> list[str]:
    current = [summary for summary in summaries if summary]
    while len(current) > 1:
        batches = _pack(current, _SECTION_BUDGET_TOKENS, counter)
        if len(batches) == len(current):
            break
        current = [_merge_batch(batch, lang, client, counter) for batch in batches]
        if counter.count("\n\n".join(current)) <= client.input_budget_tokens:
            break
    return current


def _pack(items: list[str], budget_tokens: int, counter: TokenCounter) -> list[list[str]]:
    batches: list[list[str]] = []
    pending: list[str] = []
    pending_tokens = 0
    for item in items:
        tokens = counter.count(item)
        if pending and pending_tokens + tokens > budget_tokens:
            batches.append(pending)
            pending = []
            pending_tokens = 0
        pending.append(item)
        pending_tokens += tokens
    if pending:
        batches.append(pending)
    return batches


def _merge_batch(
    batch: list[str], lang: str, client: LLMClient, counter: TokenCounter
) -> str:
    if len(batch) > 1 and counter.count(_join(batch)) > client.input_budget_tokens:
        mid = len(batch) // 2
        left = _merge_batch(batch[:mid], lang, client, counter)
        right = _merge_batch(batch[mid:], lang, client, counter)
        return _merge_direct([left, right], lang, client)
    return _merge_direct(batch, lang, client)


def _merge_direct(items: list[str], lang: str, client: LLMClient) -> str:
    completion = client.complete(
        _merge_messages(items, lang), max_output_tokens=_SECTION_OUTPUT_TOKENS
    )
    return completion.text.strip()


def _final(sections: list[str], lang: str, client: LLMClient, target_words: int) -> str:
    body = "\n\n".join(sections)
    completion = client.complete(
        _final_messages(body, lang, target_words),
        max_output_tokens=_output_tokens(target_words),
    )
    return completion.text.strip()


def _output_tokens(words: int) -> int:
    return max(_MIN_OUTPUT_TOKENS, min(words * _TOKENS_PER_WORD, _MAX_OUTPUT_TOKENS))


def _join(items: list[str]) -> str:
    return "\n\n".join(items)


def _map_messages(
    chunk_text: str, story_so_far: str, target_words: int, lang: str
) -> list[Message]:
    context = f"Story so far:\n{story_so_far}\n\n" if story_so_far else ""
    return [
        {
            "role": "system",
            "content": (
                "You summarize a book chunk for an audio summary. Write plain prose "
                f"in language '{lang}', about {target_words} words, no headings or lists."
            ),
        },
        {"role": "user", "content": f"{context}Chunk:\n{chunk_text}"},
    ]


def _merge_messages(items: list[str], lang: str) -> list[Message]:
    joined = "\n\n".join(items)
    return [
        {
            "role": "system",
            "content": (
                "Merge these partial book summaries into one coherent section "
                f"summary in language '{lang}'. Plain prose, no headings or lists."
            ),
        },
        {"role": "user", "content": joined},
    ]


def _final_messages(body: str, lang: str, target_words: int) -> list[Message]:
    return [
        {
            "role": "system",
            "content": (
                "Write a book summary of about "
                f"{target_words} words in language '{lang}' as 2 to 5 paragraphs of "
                "plain prose covering premise, key developments, characters and conclusion."
            ),
        },
        {"role": "user", "content": body},
    ]
