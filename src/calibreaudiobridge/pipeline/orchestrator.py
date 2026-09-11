"""Per-book orchestration: extract -> variants -> attach (PLAN.md 4, 5.4).

Attach is the transaction point: statuses flip to done only after
`calibredb add_format` succeeded, so an interrupted run leaves `processing`
behind and the next run retries cleanly (cache makes rework nearly free).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from .. import __version__
from ..audio.mux import (
    ChapterAudio,
    M4BSpec,
    build_m4b_command,
    build_mp3_command,
    run_ffmpeg,
    wav_duration_ms,
    write_concat_list,
    write_ffmetadata,
)
from ..calibre.extract import Extractor
from ..config import config_hash
from ..enums import JobStatus, Variant
from ..errors import CabError, ExtractionError, ExtractionReason
from ..llm.chunking import TokenCounter, chunk_chapters
from ..llm.client import LLMClient
from ..llm.preprocess import preprocess
from ..llm.summarize import summarize
from ..state import Job, Ledger
from ..tts import TTSCache, TTSEngine, synthesize_long
from .engines import resolve_engine

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

    from ..calibre.client import CalibreBook, CalibreClient
    from ..calibre.extract import ExtractedBook
    from ..calibre.scanner import Candidate
    from ..config import Config

EngineFactory = Callable[[str], TTSEngine]


@dataclass(frozen=True, slots=True)
class VariantOutcome:
    """Result of generating one variant for one book."""

    variant: Variant
    status: JobStatus
    detail: str = ""


@dataclass(frozen=True, slots=True)
class BookOutcome:
    """Result of processing one candidate book."""

    book_id: int
    title: str
    outcomes: tuple[VariantOutcome, ...]
    worked: bool = False

    def all_succeeded(self) -> bool:
        """Report whether no variant ended in failed."""
        return all(
            o.status is not JobStatus.FAILED for o in self.outcomes
        ) and bool(self.outcomes)


class Orchestrator:
    """Runs the two variant pipelines for one candidate at a time."""

    def __init__(
        self,
        *,
        cfg: Config,
        client: CalibreClient,
        ledger: Ledger,
        http: httpx.Client | None = None,
        engine_factory: EngineFactory | None = None,
    ) -> None:
        self._cfg = cfg
        self._client = client
        self._ledger = ledger
        self._llm = LLMClient(cfg.llm.models, cfg.llm, http)
        self._engine_factory = engine_factory or _default_engine_factory(cfg)
        self._extractor = Extractor(cfg.calibre, cfg.pipeline.work_dir)
        self._cache = TTSCache(cfg.pipeline.work_dir / "tts-cache")
        self._engines: dict[str, TTSEngine] = {}
        self._config_hash = config_hash(cfg)

    def process(self, candidate: Candidate) -> BookOutcome:
        """Generate every still-needed variant for one book."""
        wanted = [
            variant
            for variant, needed in (
                (Variant.AUDIOBOOK, candidate.needs_audiobook),
                (Variant.SUMMARY, candidate.needs_summary),
            )
            if needed
        ]
        outcomes: list[VariantOutcome] = []
        pending: list[Variant] = []
        for variant in wanted:
            cached = self._already_done(candidate.book.id, variant)
            if cached is not None:
                outcomes.append(cached)
            else:
                pending.append(variant)
        if not pending:
            return BookOutcome(
                candidate.book.id, candidate.book.title, tuple(outcomes), worked=False
            )
        try:
            extracted = self._extractor.extract(self._client, candidate)
        except ExtractionError as exc:
            status = _skip_status_for(exc)
            for variant in pending:
                self._record(candidate.book.id, variant, status, exc.reason.value)
                outcomes.append(VariantOutcome(variant, status, exc.reason.value))
            return BookOutcome(
                candidate.book.id, candidate.book.title, tuple(outcomes), worked=True
            )
        for variant in pending:
            match variant:
                case Variant.AUDIOBOOK:
                    outcomes.append(self._audiobook(candidate.book, extracted))
                case Variant.SUMMARY:
                    outcomes.append(self._summary(candidate.book, extracted))
        return BookOutcome(
            candidate.book.id, extracted.title, tuple(outcomes), worked=True
        )

    def _already_done(self, book_id: int, variant: Variant) -> VariantOutcome | None:
        job = self._ledger.get_job(book_id, variant)
        done = (
            job is not None
            and job.status is JobStatus.DONE
            and job.config_hash == self._config_hash
        )
        if done:
            return VariantOutcome(variant, JobStatus.DONE, "already done")
        return None

    def _audiobook(self, book: CalibreBook, extracted: ExtractedBook) -> VariantOutcome:
        variant = Variant.AUDIOBOOK
        book_id = book.id
        self._reserve(book_id, variant)
        try:
            counter = TokenCounter()
            processed = preprocess(
                extracted.chapters,
                language=extracted.language,
                counter=counter,
                client=self._llm,
                cfg=self._cfg.preprocess,
            )
            engine = self._engine_for(extracted.language)
            voice = self._voice_for(engine, extracted.language)
            staging = self._staging(book_id, variant)
            audios: list[ChapterAudio] = []
            for source, chapter in zip(extracted.chapters, processed, strict=True):
                wav = self._cached_synthesis(
                    chapter.text,
                    engine=engine,
                    voice=voice,
                    lang=extracted.language,
                    dest=staging / f"ch{source.index:03d}.wav",
                )
                audios.append(ChapterAudio(source.title, wav, wav_duration_ms(wav)))
            concat = write_concat_list([a.wav for a in audios], staging / "concat.txt")
            metadata = write_ffmetadata(audios, staging / "chapters.txt")
            output = staging / "audiobook.m4b"
            run_ffmpeg(
                self._cfg.audio,
                build_m4b_command(
                    M4BSpec(concat, metadata, output, self._tags(book, extracted, variant)),
                    self._cfg.audio,
                ),
            )
            self._client.add_format(book_id, output, replace=True)
        except CabError as exc:
            self._record(book_id, variant, JobStatus.FAILED, str(exc))
            return VariantOutcome(variant, JobStatus.FAILED, str(exc))
        self._record(book_id, variant, JobStatus.DONE, "ok")
        return VariantOutcome(variant, JobStatus.DONE)

    def _summary(self, book: CalibreBook, extracted: ExtractedBook) -> VariantOutcome:
        variant = Variant.SUMMARY
        book_id = book.id
        self._reserve(book_id, variant)
        try:
            counter = TokenCounter()
            chunks = chunk_chapters(
                extracted.chapters, self._llm.input_budget_tokens, counter
            )
            text = summarize(
                chunks,
                language=extracted.language,
                client=self._llm,
                cfg=self._cfg.summary,
                counter=counter,
            )
            engine = self._engine_for(extracted.language)
            voice = self._voice_for(engine, extracted.language)
            staging = self._staging(book_id, variant)
            wav = self._cached_synthesis(
                text,
                engine=engine,
                voice=voice,
                lang=extracted.language,
                dest=staging / "summary.wav",
            )
            output = staging / "summary.mp3"
            run_ffmpeg(
                self._cfg.audio,
                build_mp3_command(
                    wav=wav, output=output, cfg=self._cfg.audio,
                    tags=self._tags(book, extracted, variant),
                ),
            )
            self._client.add_format(book_id, output, replace=True)
        except CabError as exc:
            self._record(book_id, variant, JobStatus.FAILED, str(exc))
            return VariantOutcome(variant, JobStatus.FAILED, str(exc))
        self._record(book_id, variant, JobStatus.DONE, "ok")
        return VariantOutcome(variant, JobStatus.DONE)

    def _cached_synthesis(
        self,
        text: str,
        *,
        engine: TTSEngine,
        voice: str,
        lang: str,
        dest: Path,
    ) -> Path:
        key = self._cache.key(
            engine=engine.name, voice=voice, speed=self._cfg.tts.speed, lang=lang, text=text
        )
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        synthesize_long(
            text,
            engine,
            voice=voice,
            lang=lang,
            speed=self._cfg.tts.speed,
            sentence_pause_ms=self._cfg.tts.sentence_pause_ms,
            paragraph_pause_ms=self._cfg.tts.paragraph_pause_ms,
            out_path=dest,
        )
        return self._cache.put(key, dest)

    def _engine_for(self, lang: str) -> TTSEngine:
        if lang not in self._engines:
            self._engines[lang] = self._engine_factory(lang)
        return self._engines[lang]

    def _voice_for(self, engine: TTSEngine, lang: str) -> str:
        configured = self._cfg.tts.voices.get(lang)
        if configured:
            return configured
        voices = engine.voices(lang)
        return voices[0] if voices else "default"

    def _tags(
        self, book: CalibreBook, extracted: ExtractedBook, variant: Variant
    ) -> dict[str, str]:
        suffix = "Audiobook" if variant is Variant.AUDIOBOOK else "Summary"
        return {
            "title": f"{extracted.title} ({suffix})",
            "artist": ", ".join(book.authors),
            "album": extracted.title,
            "comment": f"CalibreAudioBridge {__version__}; engine="
            f"{self._engine_for(extracted.language).name}",
        }

    def _staging(self, book_id: int, variant: Variant) -> Path:
        staging = self._cfg.pipeline.work_dir / "staging" / str(book_id) / variant.value
        staging.mkdir(parents=True, exist_ok=True)
        return staging

    def _reserve(self, book_id: int, variant: Variant) -> None:
        job = self._ledger.get_job(book_id, variant)
        attempts = job.attempts + 1 if job is not None else 1
        self._ledger.put_job(
            Job(
                book_id=book_id,
                variant=variant,
                status=JobStatus.PROCESSING,
                attempts=attempts,
                config_hash=self._config_hash,
                started=datetime.now(UTC).isoformat(timespec="seconds"),
            )
        )
        self._set_status(book_id, variant, JobStatus.PROCESSING)

    def _record(self, book_id: int, variant: Variant, status: JobStatus, detail: str) -> None:
        job = self._ledger.get_job(book_id, variant)
        attempts = job.attempts if job is not None else 1
        self._ledger.put_job(
            Job(
                book_id=book_id,
                variant=variant,
                status=status,
                attempts=attempts,
                config_hash=self._config_hash,
                started=job.started if job is not None else None,
                finished=datetime.now(UTC).isoformat(timespec="seconds"),
                error=detail if status is JobStatus.FAILED else None,
            )
        )
        self._set_status(book_id, variant, status)

    def _set_status(self, book_id: int, variant: Variant, status: JobStatus) -> None:
        column = "cab_audiobook_status" if variant is Variant.AUDIOBOOK else "cab_summary_status"
        self._client.set_custom(column, book_id, status.value)
        if status in (JobStatus.DONE, JobStatus.FAILED):
            self._client.set_custom("cab_last_run", book_id, datetime.now(UTC).isoformat())
            self._client.set_custom(
                "cab_details",
                book_id,
                json.dumps({"status": status.value, "engine_chain": dict(self._cfg.tts.routing)}),
            )


def _skip_status_for(exc: ExtractionError) -> JobStatus:
    match exc.reason:
        case ExtractionReason.DRM:
            return JobStatus.SKIPPED_DRM
        case ExtractionReason.THIN_TEXT:
            return JobStatus.SKIPPED_NO_TEXT
        case ExtractionReason.CONVERT_FAILED:
            return JobStatus.FAILED


def _default_engine_factory(cfg: Config) -> EngineFactory:
    def factory(lang: str) -> TTSEngine:
        return resolve_engine(cfg.tts, lang)

    return factory


def requeue_failures(ledger: Ledger, client: CalibreClient) -> int:
    """Reset failed jobs to pending (cab retry); returns count."""
    count = 0
    for job in ledger.jobs([JobStatus.FAILED]):
        ledger.put_job(
            Job(
                book_id=job.book_id,
                variant=job.variant,
                status=JobStatus.PENDING,
                attempts=job.attempts,
                config_hash=job.config_hash,
            )
        )
        client.set_custom(
            "cab_audiobook_status" if job.variant is Variant.AUDIOBOOK else "cab_summary_status",
            job.book_id,
            JobStatus.PENDING.value,
        )
        count += 1
    return count
