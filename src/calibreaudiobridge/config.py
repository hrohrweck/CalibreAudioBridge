"""Layered configuration: defaults <- TOML file <- CAB__SECTION__KEY env (PLAN.md D3).

Boundary object: everything downstream receives a validated frozen Config.
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from pathlib import Path
from typing import Any, Final

import platformdirs
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import ConfigError

_SECTION_KEY_LEN: Final = 2

KNOWN_ENGINES: frozenset[str] = frozenset({"kokoro", "piper", "luxtts", "stub"})


class _Cfg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CalibreConfig(_Cfg):
    """Calibre access: library path, optional Content Server URL, binary paths."""

    library_path: Path
    server_url: str | None = None
    restrict_tag: str = ""
    calibredb_path: str = "calibredb"
    ebook_convert_path: str = "ebook-convert"


class LLMModelConfig(_Cfg):
    """One entry in the ordered model chain (PLAN.md 6.4)."""

    name: str
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    api_key_env: str | None = None
    max_context_tokens: int = Field(default=8192, gt=512)
    price_per_mtok: float = 0.0


class LLMConfig(_Cfg):
    """Ordered model chain with tuning knobs."""

    models: tuple[LLMModelConfig, ...] = ()
    concurrency: int = Field(default=2, ge=1)
    output_reserve_tokens: int = Field(default=2048, ge=0)
    max_attempts_per_model: int = Field(default=3, ge=1)


class PreprocessConfig(_Cfg):
    """LLM listenability-pass guard rails (PLAN.md 6.2)."""

    enabled: bool = True
    drift_min_ratio: float = Field(default=0.7, gt=0)
    drift_max_ratio: float = Field(default=1.3, ge=1)


class SummaryConfig(_Cfg):
    """Target listening length for summaries."""

    target_minutes: int = Field(default=30, ge=1)
    words_per_minute: int = Field(default=150, ge=50)


class LuxTTSConfig(_Cfg):
    """LuxTTS-specific knobs (English voice cloning)."""

    reference_wav: Path | None = None
    num_steps: int = Field(default=4, ge=1)


class TTSConfig(_Cfg):
    """Engine selection, voices, routing table, and prosody knobs."""

    default_engine: str = "kokoro"
    speed: float = Field(default=1.0, gt=0)
    sentence_pause_ms: int = Field(default=250, ge=0)
    paragraph_pause_ms: int = Field(default=600, ge=0)
    voices: dict[str, str] = Field(default_factory=lambda: {"en": "af_heart", "de": "df_anna"})
    routing: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "en": ["kokoro", "piper"],
            "de": ["kokoro", "piper"],
            "default": ["kokoro", "piper", "luxtts"],
        }
    )
    piper_path: str = "piper"
    piper_model_dir: Path | None = None
    luxtts: LuxTTSConfig = Field(default_factory=LuxTTSConfig)


class AudioConfig(_Cfg):
    """ffmpeg encoding knobs."""

    ffmpeg_path: str = "ffmpeg"
    m4b_bitrate: str = "64k"
    mp3_bitrate: str = "96k"
    loudnorm_target: str = "I=-16:TP=-1.5:LRA=11"


class PipelineConfig(_Cfg):
    """Run-loop limits and state location."""

    time_budget_minutes: int = Field(default=240, ge=1)
    max_books_per_run: int = Field(default=3, ge=1)
    work_dir: Path = Field(
        default_factory=lambda: Path(platformdirs.user_state_dir("calibreaudiobridge"))
    )


class Config(_Cfg):
    """Root configuration object."""

    calibre: CalibreConfig
    llm: LLMConfig = Field(default_factory=LLMConfig)
    preprocess: PreprocessConfig = Field(default_factory=PreprocessConfig)
    summary: SummaryConfig = Field(default_factory=SummaryConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)


def load_config(path: Path | None) -> Config:
    """Load defaults <- TOML <- env CAB__SECTION__KEY, then validate."""
    data: dict[str, Any] = {}
    if path is not None and path.is_file():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    data = _apply_env_overrides(data)
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError("; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )) from exc


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:  # boundary: TOML/env parse
    section_names = set(Config.model_fields)
    for raw_key, raw_value in os.environ.items():
        if not raw_key.startswith("CAB__"):
            continue
        parts = raw_key.removeprefix("CAB__").lower().split("__", 1)
        if len(parts) != _SECTION_KEY_LEN or parts[0] not in section_names:
            continue
        section, key = parts
        try:
            parsed: Any = json.loads(raw_value)
        except json.JSONDecodeError:
            parsed = raw_value
        data.setdefault(section, {})
        if isinstance(data[section], dict):
            data[section][key] = parsed
    return data


def config_hash(cfg: Config) -> str:
    """Hash of the pipeline-relevant config subset; invalidates caches/outputs."""
    subset = {
        "llm_models": [m.name for m in cfg.llm.models],
        "preprocess_enabled": cfg.preprocess.enabled,
        "tts_speed": cfg.tts.speed,
        "tts_voices": dict(sorted(cfg.tts.voices.items())),
        "m4b_bitrate": cfg.audio.m4b_bitrate,
        "mp3_bitrate": cfg.audio.mp3_bitrate,
        "target_minutes": cfg.summary.target_minutes,
    }
    blob = json.dumps(subset, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()
