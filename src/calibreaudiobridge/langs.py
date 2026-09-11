"""Language normalization: calibre stores ISO 639-2 ('eng'); engines want 'en'."""

from __future__ import annotations

from typing import Final

ISO3_TO_1: Final[dict[str, str]] = {
    "eng": "en",
    "deu": "de",
    "ger": "de",
    "fra": "fr",
    "fre": "fr",
    "spa": "es",
    "ita": "it",
    "nld": "nl",
    "dut": "nl",
    "por": "pt",
    "rus": "ru",
    "pol": "pl",
    "swe": "sv",
    "nor": "no",
    "dan": "da",
    "fin": "fi",
    "ces": "cs",
    "cze": "cs",
    "ell": "el",
    "gre": "el",
    "tur": "tr",
    "jpn": "ja",
    "kor": "ko",
    "zho": "zh",
    "chi": "zh",
    "ara": "ar",
    "hin": "hi",
}

DEFAULT_LANGUAGE: Final = "en"
_SHORT_CODE_LEN: Final = 2


def normalize_language(lang: str) -> str:
    """Map an ISO 639-2/B code to ISO 639-1; pass short codes through."""
    code = lang.strip().lower()
    if code in ISO3_TO_1:
        return ISO3_TO_1[code]
    if len(code) == _SHORT_CODE_LEN and code.isalpha():
        return code
    return DEFAULT_LANGUAGE
