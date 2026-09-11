"""Single-instance lock so overlapping cron runs no-op (PLAN.md D8)."""

from __future__ import annotations

import fcntl
import os
from typing import TYPE_CHECKING, Self

from .errors import LockHeldError

if TYPE_CHECKING:
    from pathlib import Path


class InstanceLock:
    """Exclusive, non-blocking flock guard around one lock file."""

    def __init__(self, path: Path) -> None:
        """Store lock path; nothing is touched until acquire()."""
        self._path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        """Take the lock or raise LockHeldError. Idempotent per instance."""
        if self._fd is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise LockHeldError(str(self._path)) from None
        self._fd = fd

    def release(self) -> None:
        """Release the lock if held. No-op otherwise."""
        if self._fd is None:
            return
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
