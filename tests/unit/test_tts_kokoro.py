"""Kokoro adapter: voice catalog + unavailable guard (kokoro stays uninstalled)."""

from __future__ import annotations

import importlib.util

import pytest

from calibreaudiobridge.config import TTSConfig
from calibreaudiobridge.tts.base import TTSUnavailableError
from calibreaudiobridge.tts.kokoro import KOKORO_VOICES, KokoroEngine, kokoro_voices

_KOKORO_INSTALLED = (
    importlib.util.find_spec("kokoro") is not None
    or importlib.util.find_spec("soundfile") is not None
)


class TestKokoroVoices_whenCatalogQueried:
    def test_catalog_matches_spec(self) -> None:
        assert KOKORO_VOICES == {
            "en": ("af_heart", "af_alloy", "am_fenrir", "am_michael"),
            "de": ("df_anna", "df_amadeus", "dm_arthur"),
            "other": ("af_heart",),
        }

    def test_known_langs_return_their_entry(self) -> None:
        assert kokoro_voices("en") == ("af_heart", "af_alloy", "am_fenrir", "am_michael")
        assert kokoro_voices("de") == ("df_anna", "df_amadeus", "dm_arthur")

    def test_unknown_lang_falls_back_to_en_default(self) -> None:
        assert kokoro_voices("zz") == ("af_heart",)


class TestKokoroEngine_whenExtrasNotInstalled:
    @pytest.mark.skipif(_KOKORO_INSTALLED, reason="kokoro/soundfile extras are installed")
    def test_constructor_raises_typed_unavailable(self) -> None:
        with pytest.raises(TTSUnavailableError) as excinfo:
            KokoroEngine(TTSConfig())
        assert excinfo.value.engine == "kokoro"
        assert "kokoro" in str(excinfo.value)
