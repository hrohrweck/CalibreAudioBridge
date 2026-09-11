"""Engine routing: first available engine in the language's chain narrates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..tts import KokoroEngine, PiperEngine, StubEngine, TTSEngine, TTSUnavailableError

if TYPE_CHECKING:
    from ..config import TTSConfig


def resolve_engine(cfg: TTSConfig, lang: str) -> TTSEngine:
    """Build the first available engine from tts.routing for `lang`.

    Raises TTSUnavailableError when every engine in the chain is unavailable.
    """
    chain = cfg.routing.get(lang) or cfg.routing.get("default") or ()
    tried: list[str] = []
    for name in chain:
        factory = _FACTORIES.get(name)
        if factory is None:
            continue
        try:
            return factory(cfg)
        except TTSUnavailableError:
            tried.append(name)
    raise TTSUnavailableError(f"routing[{lang}]={'/'.join(tried) or 'empty'}")


def _make_kokoro(cfg: TTSConfig) -> TTSEngine:
    return KokoroEngine(cfg)


def _make_piper(cfg: TTSConfig) -> TTSEngine:
    engine = PiperEngine(cfg)
    if not engine.is_available():
        raise TTSUnavailableError("piper")
    return engine


def _make_stub(_cfg: TTSConfig) -> TTSEngine:
    return StubEngine()


_FACTORIES = {
    "kokoro": _make_kokoro,
    "piper": _make_piper,
    "stub": _make_stub,
}
