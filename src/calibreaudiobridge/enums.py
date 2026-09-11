"""Shared enums: audio variants and job statuses."""

from __future__ import annotations

from enum import StrEnum


class Variant(StrEnum):
    """The two audio variants generated per book (PLAN.md D2)."""

    AUDIOBOOK = "audiobook"
    SUMMARY = "summary"


class JobStatus(StrEnum):
    """Per-variant processing status; mirrors the #cab_* custom columns."""

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    SKIPPED_DRM = "skipped_drm"
    SKIPPED_NO_TEXT = "skipped_no_text"
    SKIPPED_MANUAL = "skipped_manual"
