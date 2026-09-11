"""InstanceLock: overlapping runs must fail fast (PLAN.md D8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from calibreaudiobridge.errors import LockHeldError
from calibreaudiobridge.lock import InstanceLock


class TestInstanceLock_whenHeld:
    def test_second_acquire_raises(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "cab.lock"
        first = InstanceLock(lock_path)
        first.acquire()
        with pytest.raises(LockHeldError):
            InstanceLock(lock_path).acquire()
        first.release()

    def test_release_allows_reacquire(self, tmp_path: Path) -> None:
        lock = InstanceLock(tmp_path / "cab.lock")
        lock.acquire()
        lock.release()
        InstanceLock(tmp_path / "cab.lock").acquire()

    def test_context_manager_releases(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "cab.lock"
        with InstanceLock(lock_path):
            pass
        InstanceLock(lock_path).acquire()
