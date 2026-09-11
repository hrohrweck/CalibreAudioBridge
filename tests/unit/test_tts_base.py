"""Long-text synthesis: sentence splitting, silence gaps, retries, substitution."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from calibreaudiobridge.tts.base import (
    ATTEMPTS_PER_SENTENCE,
    Segment,
    TTSSynthesisError,
    silence_wav,
    split_sentences,
    synthesize_long,
)
from calibreaudiobridge.tts.stub import StubEngine

RATE = 22050
SENTENCE_MS = 100  # _ScriptedEngine writes 100 ms per sentence, 22050 Hz mono


def _frames(ms: int) -> int:
    return ms * RATE // 1000


def _format(path: Path) -> tuple[int, int, int]:
    with wave.open(str(path), "rb") as wav:
        return wav.getnchannels(), wav.getsampwidth(), wav.getframerate()


def _nframes(path: Path) -> int:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes()


class _ScriptedEngine:
    """Test double: fixed 100 ms silence wavs, scriptable failures and rates."""

    name = "scripted"

    def __init__(
        self,
        *,
        fail_texts: frozenset[str] | None = None,
        fail_times: dict[str, int] | None = None,
        rate_for: dict[str, int] | None = None,
    ) -> None:
        self.calls: list[str] = []
        self._fail_texts = fail_texts or frozenset()
        self._fail_times = dict(fail_times or {})
        self._rate_for = rate_for or {}

    def voices(self, lang: str) -> list[str]:
        _ = lang
        return ["scripted_x"]

    def synthesize(
        self, text: str, *, voice: str, lang: str, speed: float, out_path: Path
    ) -> Path:
        _ = (voice, lang, speed)
        self.calls.append(text)
        if text in self._fail_texts or self._fail_times.get(text, 0) > 0:
            if self._fail_times.get(text, 0) > 0:
                self._fail_times[text] -= 1
            raise TTSSynthesisError(self.name, "boom")
        return silence_wav(out_path, SENTENCE_MS, sample_rate=self._rate_for.get(text, RATE))


class TestSplitSentences_whenSingleParagraph:
    def test_sentences_and_last_ends_paragraph(self) -> None:
        given = "One two three. Four five!"
        segments = split_sentences(given)
        assert segments == [
            Segment(text="One two three.", ends_paragraph=False),
            Segment(text="Four five!", ends_paragraph=True),
        ]

    def test_text_without_terminal_punctuation_is_one_sentence(self) -> None:
        assert split_sentences("no punctuation here") == [
            Segment(text="no punctuation here", ends_paragraph=True)
        ]


class TestSplitSentences_whenMultipleParagraphs:
    def test_paragraph_tail_takes_the_longer_pause_flag(self) -> None:
        given = "A one. A two.\n\nB one. B two."
        segments = split_sentences(given)
        assert [(s.text, s.ends_paragraph) for s in segments] == [
            ("A one.", False),
            ("A two.", True),
            ("B one.", False),
            ("B two.", True),
        ]


class TestSplitSentences_whenMessyWhitespace:
    def test_linebreaks_and_spaces_collapse(self) -> None:
        segments = split_sentences("A sentence\nspans a line.  Another   here.")
        assert [s.text for s in segments] == ["A sentence spans a line.", "Another here."]

    def test_blank_only_input_yields_no_segments(self) -> None:
        assert split_sentences("  \n\n \n ") == []


class TestSilenceWav_whenDefaultParams:
    def test_writes_mono_16bit_22050_with_requested_duration(self, tmp_path: Path) -> None:
        out = silence_wav(tmp_path / "silence.wav", 250)
        assert _format(out) == (1, 2, RATE)
        assert _nframes(out) == _frames(250)


class TestSilenceWav_whenCustomParams:
    def test_honors_rate_and_width(self, tmp_path: Path) -> None:
        out = silence_wav(tmp_path / "silence.wav", 100, sample_rate=24000, sample_width=2)
        assert _format(out) == (1, 2, 24000)
        assert _nframes(out) == 100 * 24000 // 1000


class TestSynthesizeLong_whenStubEngine:
    def test_concatenates_sentences_and_pauses_exactly(self, tmp_path: Path) -> None:
        given = "One two three. Four five!\n\nSix seven."
        out_path = tmp_path / "long.wav"
        # s(100) + pause_s(200) + s(100) + pause_p(500) + s(100) = 1000 ms
        result = synthesize_long(
            given,
            StubEngine(),
            voice="stub_en",
            lang="en",
            speed=1.0,
            sentence_pause_ms=200,
            paragraph_pause_ms=500,
            out_path=out_path,
        )
        assert result == out_path
        assert _format(out_path) == (1, 2, RATE)
        assert _nframes(out_path) == _frames(1000)

    def test_zero_pauses_yield_bare_sentence_audio(self, tmp_path: Path) -> None:
        given = "One two three. Four five!"
        out_path = tmp_path / "nopauses.wav"
        synthesize_long(
            given,
            StubEngine(),
            voice="stub_en",
            lang="en",
            speed=1.0,
            sentence_pause_ms=0,
            paragraph_pause_ms=0,
            out_path=out_path,
        )
        nframes = _nframes(out_path)
        assert nframes == _frames(2 * SENTENCE_MS)


class TestSynthesizeLong_whenSentencePersistentlyFails:
    def test_substitutes_one_second_silence_and_continues(self, tmp_path: Path) -> None:
        given = "Good one. Bad two. Good three."
        engine = _ScriptedEngine(fail_texts=frozenset({"Bad two."}))
        out_path = tmp_path / "gap.wav"
        synthesize_long(
            given,
            engine,
            voice="v",
            lang="en",
            speed=1.0,
            sentence_pause_ms=0,
            paragraph_pause_ms=0,
            out_path=out_path,
        )
        # good(100) + failed->1000ms silence + good(100)
        nframes = _nframes(out_path)
        assert nframes == _frames(2 * SENTENCE_MS + 1000)
        # 3 attempts for the failing sentence, 1 each for the good ones
        assert len(engine.calls) == len(split_sentences(given)) - 1 + ATTEMPTS_PER_SENTENCE


class TestSynthesizeLong_whenTransientFailureRecovers:
    def test_third_attempt_success_needs_no_substitution(self, tmp_path: Path) -> None:
        given = "Flaky sentence here."
        engine = _ScriptedEngine(fail_times={"Flaky sentence here.": 2})
        out_path = tmp_path / "flaky.wav"
        synthesize_long(
            given,
            engine,
            voice="v",
            lang="en",
            speed=1.0,
            sentence_pause_ms=0,
            paragraph_pause_ms=0,
            out_path=out_path,
        )
        nframes = _nframes(out_path)
        assert nframes == _frames(SENTENCE_MS)  # real audio, not the 1 s substitute
        assert len(engine.calls) == 3


class TestSynthesizeLong_whenSampleParamsMismatch:
    def test_mismatched_sentence_is_replaced_by_silence_of_same_length(
        self, tmp_path: Path
    ) -> None:
        given = "Native rate. Odd rate."
        engine = _ScriptedEngine(rate_for={"Odd rate.": 16000})
        out_path = tmp_path / "mixed.wav"
        synthesize_long(
            given,
            engine,
            voice="v",
            lang="en",
            speed=1.0,
            sentence_pause_ms=0,
            paragraph_pause_ms=0,
            out_path=out_path,
        )
        # first sentence 100 ms @22050 defines the format; the 16 kHz sentence
        # keeps its 100 ms/16000 Hz duration (1600 frames) as silence
        nframes = _nframes(out_path)
        assert nframes == _frames(SENTENCE_MS) + 100 * 16000 // 1000


class TestSynthesizeLong_whenNothingToSynthesize:
    @pytest.mark.parametrize("given", ["", "   \n\n  "])
    def test_raises_typed_error(self, given: str, tmp_path: Path) -> None:
        with pytest.raises(TTSSynthesisError, match="no sentences"):
            synthesize_long(
                given,
                StubEngine(),
                voice="stub_en",
                lang="en",
                speed=1.0,
                sentence_pause_ms=200,
                paragraph_pause_ms=500,
                out_path=tmp_path / "empty.wav",
            )
