"""Summarize: map-reduce structure, target length and budget subdivision."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, override

import pytest

from calibreaudiobridge.config import LLMConfig, SummaryConfig
from calibreaudiobridge.errors import ConfigError
from calibreaudiobridge.llm.chunking import Chunk, TokenCounter
from calibreaudiobridge.llm.client import Completion, LLMClient, Message
from calibreaudiobridge.llm.summarize import summarize

if TYPE_CHECKING:
    from collections.abc import Callable


class _CharCounter(TokenCounter):
    """Deterministic counter: one token per character."""

    @override
    def count(self, text: str) -> int:
        return len(text)


@dataclass(frozen=True, slots=True)
class _Call:
    messages: list[Message]
    max_output_tokens: int


class _FakeClient(LLMClient):
    """LLMClient stand-in returning word-proportional replies and recording calls."""

    def __init__(
        self,
        responder: Callable[[list[Message], int], str],
        *,
        budget: int = 100_000,
    ) -> None:
        super().__init__((), LLMConfig())
        self._responder = responder
        self._budget = budget
        self.calls: list[_Call] = []

    @property
    @override
    def has_models(self) -> bool:
        return True

    @property
    @override
    def input_budget_tokens(self) -> int:
        return self._budget

    @override
    def complete(self, messages: list[Message], *, max_output_tokens: int) -> Completion:
        self.calls.append(_Call(messages=messages, max_output_tokens=max_output_tokens))
        return Completion(
            text=self._responder(messages, max_output_tokens),
            model="fake",
            tokens_in=0,
            tokens_out=0,
        )


def _wordy(_messages: list[Message], max_output_tokens: int) -> str:
    return " ".join(["word"] * max(1, max_output_tokens // 2))


def _chunks(count: int, *, words: int = 100) -> list[Chunk]:
    text = " ".join(["word"] * words)
    return [
        Chunk(index=index, chapter_index=index, chapter_title=f"Ch{index}", text=text)
        for index in range(count)
    ]


class TestSummarize_whenNoModels:
    def test_raises_config_error(self) -> None:
        client = LLMClient((), LLMConfig())
        with pytest.raises(ConfigError):
            summarize(
                _chunks(3),
                language="en",
                client=client,
                cfg=SummaryConfig(),
                counter=_CharCounter(),
            )


class TestSummarize_whenNoChunks:
    def test_returns_empty_and_makes_no_calls(self) -> None:
        client = _FakeClient(_wordy)
        result = summarize(
            [], language="en", client=client, cfg=SummaryConfig(), counter=_CharCounter()
        )
        assert result == ""
        assert client.calls == []


class TestSummarize_whenMapReduce:
    def test_calls_map_then_reduce_then_final(self) -> None:
        cfg = SummaryConfig(target_minutes=1, words_per_minute=150)
        client = _FakeClient(_wordy)
        summarize(
            _chunks(3, words=100),
            language="en",
            client=client,
            cfg=cfg,
            counter=_CharCounter(),
        )
        assert len(client.calls) >= 4

    def test_hits_target_length_within_thirty_percent(self) -> None:
        cfg = SummaryConfig(target_minutes=1, words_per_minute=150)
        client = _FakeClient(_wordy)
        result = summarize(
            _chunks(3, words=100),
            language="en",
            client=client,
            cfg=cfg,
            counter=_CharCounter(),
        )
        target_words = cfg.target_minutes * cfg.words_per_minute
        produced = len(result.split())
        assert abs(produced - target_words) / target_words <= 0.3

    def test_final_call_sizes_output_to_target(self) -> None:
        cfg = SummaryConfig(target_minutes=1, words_per_minute=150)
        client = _FakeClient(_wordy)
        summarize(
            _chunks(2, words=100),
            language="en",
            client=client,
            cfg=cfg,
            counter=_CharCounter(),
        )
        assert client.calls[-1].max_output_tokens == 300


class TestSummarize_whenInputExceedsBudget:
    def test_subdivides_merge_batches(self) -> None:
        cfg = SummaryConfig(target_minutes=1, words_per_minute=150)
        client = _FakeClient(_wordy, budget=200)
        summarize(
            _chunks(4, words=100),
            language="en",
            client=client,
            cfg=cfg,
            counter=_CharCounter(),
        )
        # 4 map calls, then the too-large merge batch is binary-split into
        # several smaller merge calls before the final call.
        assert len(client.calls) >= 6
