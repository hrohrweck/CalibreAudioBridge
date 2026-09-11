"""Chunker: token counting, sentence splitting and budget packing."""

from __future__ import annotations

from typing import override

from calibreaudiobridge.calibre.extract import Chapter
from calibreaudiobridge.llm.chunking import (
    TokenCounter,
    chunk_chapters,
    split_sentences,
)


class _CharCounter(TokenCounter):
    """Deterministic counter: one token per character."""

    @override
    def count(self, text: str) -> int:
        return len(text)


def _chapter(index: int, text: str, title: str = "Chapter") -> Chapter:
    return Chapter(index=index, title=title, text=text)


class TestTokenCounter_whenCounting:
    def test_counts_positive_tokens_for_text(self) -> None:
        assert TokenCounter().count("Hello world, this is a test.") > 0

    def test_empty_text_counts_zero(self) -> None:
        assert TokenCounter().count("") == 0

    def test_falls_back_to_chars_over_four(self, monkeypatch) -> None:
        monkeypatch.setattr("calibreaudiobridge.llm.chunking._load_encoding", lambda: None)
        assert TokenCounter().count("abcdefgh") == 2


class TestSplitSentences_whenBasic:
    def test_splits_two_sentences_keeping_punctuation(self) -> None:
        assert split_sentences("One thing. Two things!") == ["One thing.", "Two things!"]

    def test_does_not_split_abbreviation_or_decimal(self) -> None:
        result = split_sentences("Dr. Smith paid 3.14 dollars. Then he left.")
        assert result == ["Dr. Smith paid 3.14 dollars.", "Then he left."]

    def test_does_not_split_inside_parentheses(self) -> None:
        result = split_sentences("He left (and came back. Later.) Then it rained.")
        assert result == ["He left (and came back. Later.)", "Then it rained."]

    def test_does_not_split_inside_quotes(self) -> None:
        result = split_sentences('She said "go home. now." Then she left.')
        assert result == ['She said "go home. now."', "Then she left."]

    def test_handles_german_abbreviations(self) -> None:
        result = split_sentences("Das ist z. B. gut. Danach ging er.")
        assert result == ["Das ist z. B. gut.", "Danach ging er."]

    def test_keeps_unterminated_tail(self) -> None:
        assert split_sentences("No period here") == ["No period here"]

    def test_empty_text_yields_nothing(self) -> None:
        assert split_sentences("") == []


class TestChunkChapters_whenChapterFits:
    def test_single_chunk_carries_chapter_metadata(self) -> None:
        chapters = [_chapter(3, "Short chapter.", title="Intro")]
        chunks = chunk_chapters(chapters, 1000, _CharCounter())
        assert len(chunks) == 1
        assert chunks[0].index == 0
        assert chunks[0].chapter_index == 3
        assert chunks[0].chapter_title == "Intro"
        assert chunks[0].text == "Short chapter."

    def test_empty_chapter_produces_no_chunks(self) -> None:
        assert chunk_chapters([_chapter(0, "   ")], 100, _CharCounter()) == []


class TestChunkChapters_whenParagraphPacking:
    def test_packs_paragraphs_to_budget(self) -> None:
        text = "\n\n".join(["A" * 30, "B" * 30, "C" * 30])
        chunks = chunk_chapters([_chapter(0, text)], 60, _CharCounter())
        assert [chunk.text for chunk in chunks] == ["A" * 30, "B" * 30, "C" * 30]
        assert all(len(chunk.text) <= 60 for chunk in chunks)

    def test_never_splits_mid_sentence(self) -> None:
        text = "First sentence here. Second sentence here. Third sentence here."
        chunks = chunk_chapters([_chapter(0, text)], 40, _CharCounter())
        for chunk in chunks:
            for sentence in split_sentences(chunk.text):
                assert sentence in text


class TestChunkChapters_whenSentencePacking:
    def test_sentence_packs_to_budget(self) -> None:
        text = "Alpha one. Beta two. Gamma three."
        chunks = chunk_chapters([_chapter(0, text)], 22, _CharCounter())
        assert all(len(chunk.text) <= 22 for chunk in chunks)
        assert sum(len(chunk.text.split()) for chunk in chunks) == 6

    def test_oversized_sentence_becomes_own_chunk(self) -> None:
        long_sentence = "Word " * 20 + "end."
        text = f"Short. {long_sentence}"
        chunks = chunk_chapters([_chapter(0, text)], 10, _CharCounter())
        assert long_sentence in [chunk.text for chunk in chunks]

    def test_indices_are_sequential_across_chapters(self) -> None:
        chapters = [_chapter(0, "A. B. C. D."), _chapter(1, "E. F. G. H.")]
        chunks = chunk_chapters(chapters, 6, _CharCounter())
        assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
        assert {chunk.chapter_index for chunk in chunks} == {0, 1}


class TestChunkChapters_whenRealTokenCounter:
    def test_respects_budget_for_whole_paragraphs(self) -> None:
        counter = TokenCounter()
        paragraph = "This is a sentence with several words. " * 20
        text = f"{paragraph}\n\n{paragraph}\n\n{paragraph}"
        budget = max(20, counter.count(text) // 3)
        chunks = chunk_chapters([_chapter(0, text)], budget, counter)
        for chunk in chunks:
            sentences = split_sentences(chunk.text)
            if len(sentences) > 1:
                assert counter.count(chunk.text) <= budget
