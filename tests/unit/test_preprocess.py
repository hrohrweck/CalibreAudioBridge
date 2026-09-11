"""Preprocess: Stage-1 rules, Stage-2 LLM edits, drift guard and fallbacks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from calibreaudiobridge.calibre.extract import Chapter
from calibreaudiobridge.config import LLMConfig, PreprocessConfig
from calibreaudiobridge.llm.chunking import TokenCounter
from calibreaudiobridge.llm.client import Completion, LLMClient, LLMExhaustedError, Message
from calibreaudiobridge.llm.preprocess import PreprocessedChapter, preprocess

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
    """LLMClient stand-in that records calls and replies from a responder."""

    def __init__(
        self,
        responder: Callable[[list[Message]], str],
        *,
        has_models: bool = True,
        budget: int = 1000,
    ) -> None:
        super().__init__((), LLMConfig())
        self._responder = responder
        self._has_models = has_models
        self._budget = budget
        self.calls: list[_Call] = []

    @property
    @override
    def has_models(self) -> bool:
        return self._has_models

    @property
    @override
    def input_budget_tokens(self) -> int:
        return self._budget

    @override
    def complete(self, messages: list[Message], *, max_output_tokens: int) -> Completion:
        self.calls.append(_Call(messages=messages, max_output_tokens=max_output_tokens))
        return Completion(
            text=self._responder(messages),
            model="fake",
            tokens_in=0,
            tokens_out=0,
        )


def _echo(messages: list[Message]) -> str:
    return messages[-1]["content"].split("\n\n", 1)[-1]


def _chapter(text: str, index: int = 0, title: str = "Chapter") -> Chapter:
    return Chapter(index=index, title=title, text=text)


def _preprocess(
    text: str,
    responder: Callable[[list[Message]], str],
    *,
    language: str = "en",
    cfg: PreprocessConfig | None = None,
    has_models: bool = True,
    budget: int = 1000,
) -> tuple[PreprocessedChapter, _FakeClient]:
    client = _FakeClient(responder, has_models=has_models, budget=budget)
    results = preprocess(
        [_chapter(text)],
        language=language,
        counter=_CharCounter(),
        client=client,
        cfg=cfg if cfg is not None else PreprocessConfig(),
    )
    return results[0], client


class TestStage1_whenDeterministic:
    def test_expands_integers_and_floats(self) -> None:
        result, client = _preprocess("There are 3 cats and 2.5 dogs.", _echo, has_models=False)
        assert "three" in result.text
        assert "two point five" in result.text
        assert client.calls == []

    def test_expands_german_numbers(self) -> None:
        result, _ = _preprocess("Es sind 3 Katzen.", _echo, language="de", has_models=False)
        assert "drei" in result.text

    def test_strips_footnote_markers(self) -> None:
        result, _ = _preprocess("Text[12] and [footnote] here.", _echo, has_models=False)
        assert "[12]" not in result.text
        assert "footnote" not in result.text

    def test_replaces_urls_with_english_link(self) -> None:
        result, _ = _preprocess("See https://example.com/a for more.", _echo, has_models=False)
        assert "(Link)" in result.text
        assert "https://" not in result.text

    def test_replaces_urls_with_german_link(self) -> None:
        result, _ = _preprocess("Siehe https://example.de", _echo, language="de", has_models=False)
        assert "(link)" in result.text


class TestPreprocess_whenStage2DisabledOrUnavailable:
    def test_disabled_returns_stage1_without_calls(self) -> None:
        result, client = _preprocess(
            "Three cats.", _echo, cfg=PreprocessConfig(enabled=False)
        )
        assert result.used_llm is False
        assert result.warnings == ()
        assert client.calls == []

    def test_no_models_returns_stage1_without_calls(self) -> None:
        result, client = _preprocess("Three cats.", _echo, has_models=False)
        assert result.used_llm is False
        assert result.warnings == ()
        assert client.calls == []


class TestPreprocess_whenLlmEditsWithinDrift:
    def test_uses_llm_text_and_flags_used(self) -> None:
        source = "Hello world. This is a test."
        result, client = _preprocess(source, lambda _messages: source)
        assert result.text == source
        assert result.used_llm is True
        assert result.warnings == ()
        assert len(client.calls) == 1

    def test_sizes_output_tokens_from_input(self) -> None:
        source = "Hello world."
        _, client = _preprocess(source, lambda _messages: source)
        assert client.calls[0].max_output_tokens == min(len(source) * 2, 4096)


class TestPreprocess_whenDriftRetrySucceeds:
    def test_second_reply_is_used(self) -> None:
        source = "Hello world. This is a test."
        replies = iter([source * 2, source])
        result, client = _preprocess(source, lambda _messages: next(replies))
        assert result.text == source
        assert result.used_llm is True
        assert result.warnings == ()
        assert len(client.calls) == 2


class TestPreprocess_whenDriftPersists:
    def test_falls_back_to_stage1_with_warning(self) -> None:
        source = "Hello world. This is a test."
        result, client = _preprocess(source, lambda _messages: source * 2)
        assert result.text == source
        assert result.used_llm is False
        assert result.warnings == ("llm_drift_fallback",)
        assert len(client.calls) == 2


class TestPreprocess_whenLlmUnavailable:
    def test_falls_back_with_unavailable_warning(self) -> None:
        source = "Hello world. This is a test."

        def responder(_messages: list[Message]) -> str:
            raise LLMExhaustedError("down")

        result, client = _preprocess(source, responder)
        assert result.text == source
        assert result.used_llm is False
        assert result.warnings == ("llm_unavailable_fallback",)
        assert len(client.calls) == 1


class TestPreprocess_whenChapterExceedsBudget:
    def test_chunks_chapter_and_calls_once_per_chunk(self) -> None:
        text = "Alpha one. Beta two. Gamma three. Delta four."
        result, client = _preprocess(text, _echo, budget=12)
        assert len(client.calls) > 1
        assert result.used_llm is True
        expected = "\n\n".join(_echo(call.messages) for call in client.calls)
        assert result.text == expected
