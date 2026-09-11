"""TTS cache: stable keys, sharded storage, hit/miss lifecycle."""

from __future__ import annotations

import re
from pathlib import Path

from calibreaudiobridge.tts.cache import TTSCache
from calibreaudiobridge.tts.stub import StubEngine


def _make_key(
    cache: TTSCache,
    *,
    engine: str = "stub",
    voice: str = "stub_en",
    speed: float = 1.0,
    lang: str = "en",
    text: str = "Hello.",
) -> str:
    return cache.key(engine=engine, voice=voice, speed=speed, lang=lang, text=text)


class TestCacheKey_whenInputsVary:
    def test_same_inputs_same_key_across_instances(self, tmp_path: Path) -> None:
        first = _make_key(TTSCache(tmp_path))
        second = _make_key(TTSCache(tmp_path / "elsewhere"))
        assert first == second
        assert re.fullmatch(r"[0-9a-f]{64}", first)

    def test_each_component_changes_the_key(self, tmp_path: Path) -> None:
        cache = TTSCache(tmp_path)
        base = _make_key(cache)
        variants = [
            _make_key(cache, engine="piper"),
            _make_key(cache, voice="other_voice"),
            _make_key(cache, speed=1.5),
            _make_key(cache, lang="de"),
            _make_key(cache, text="Hello!"),
        ]
        assert all(variant != base for variant in variants)
        assert len({base, *variants}) == 6


class TestCacheGetPut_whenNothingStored:
    def test_get_returns_none(self, tmp_path: Path) -> None:
        assert TTSCache(tmp_path).get("ab" + "c" * 62) is None


class TestCacheGetPut_whenWavStored:
    def test_put_copies_into_two_char_shard_and_get_finds_it(self, tmp_path: Path) -> None:
        cache = TTSCache(tmp_path)
        wav = StubEngine().synthesize(
            "cache me", voice="stub_en", lang="en", speed=1.0, out_path=tmp_path / "src.wav"
        )
        key = _make_key(cache)

        stored = cache.put(key, wav)
        assert stored == tmp_path / key[:2] / f"{key}.wav"
        assert stored.is_file()
        assert stored.read_bytes() == wav.read_bytes()
        assert cache.get(key) == stored

    def test_second_put_replaces_content(self, tmp_path: Path) -> None:
        cache = TTSCache(tmp_path)
        key = _make_key(cache)
        first = StubEngine().synthesize(
            "one", voice="stub_en", lang="en", speed=1.0, out_path=tmp_path / "one.wav"
        )
        second = StubEngine().synthesize(
            "two and a bit longer", voice="stub_en", lang="en", speed=1.0,
            out_path=tmp_path / "two.wav",
        )
        cache.put(key, first)
        cache.put(key, second)
        stored = cache.get(key)
        assert stored is not None
        assert stored.read_bytes() == second.read_bytes()
