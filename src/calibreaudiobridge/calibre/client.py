"""calibredb wrapper: JSON in/out, mode probe (server -> local), no raw sqlite."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never, final

from ..enums import JobStatus, Variant
from ..errors import CalibreBlockedError, CalibreEnvError
from ..langs import normalize_language

if TYPE_CHECKING:
    from pathlib import Path

    from ..config import CalibreConfig

LOCK_MARKER: str = "Another calibre program"
CUSTOM_FIELDS: tuple[str, str] = ("#cab_audiobook_status", "#cab_summary_status")


@final
class CalibreMode:
    """Resolved access mode (namespace of string constants)."""

    SERVER: str = "server"
    LOCAL: str = "local"


@dataclass(frozen=True, slots=True)
class CalibreBook:
    """One calibre book as seen by the scanner."""

    id: int
    title: str
    authors: tuple[str, ...]
    formats: tuple[str, ...]
    languages: tuple[str, ...]
    tags: tuple[str, ...]
    audiobook_status: str
    summary_status: str


@dataclass(frozen=True, slots=True)
class CustomColumn:
    """One custom column as listed by calibredb."""

    label: str
    name: str
    datatype: str


def _json_array(text: str) -> list[dict[str, object]]:
    """Parse calibredb JSON output or raise CalibreEnvError."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raise CalibreEnvError("calibredb list emitted invalid JSON") from None
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise CalibreEnvError("calibredb list JSON was not an array of objects")
    return raw


def _as_list(value: object) -> list[str]:
    """Calibredb JSON emits lists for authors/tags and CSV strings for formats."""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


@final
class CalibreClient:
    """Thin, typed wrapper around the calibredb CLI."""

    _cfg: CalibreConfig
    _timeout: int
    _mode: str | None
    _server_target: str | None

    def __init__(self, cfg: CalibreConfig, timeout_s: int = 600) -> None:
        """Store config; nothing runs until resolve_mode()."""
        self._cfg = cfg
        self._timeout = timeout_s
        self._mode = None
        self._server_target = None

    @property
    def mode(self) -> str | None:
        """Access mode if already resolved."""
        return self._mode

    def resolve_mode(self) -> str:
        """Probe Content Server first, then direct path (PLAN.md 5.1)."""
        if self._mode is not None:
            return self._mode
        if self._cfg.server_url and self._probe_server() is not None:
            self._mode = CalibreMode.SERVER
            return self._mode
        result = self._raw(
            str(self._cfg.library_path),
            "list",
            "--for-machine",
            "--fields",
            "id",
            "--limit",
            "1",
        )
        if result.returncode == 0:
            self._mode = CalibreMode.LOCAL
            return self._mode
        if LOCK_MARKER in (result.stderr or ""):
            raise CalibreBlockedError(str(self._cfg.library_path))
        stderr = (result.stderr or "").strip()[:300]
        raise CalibreEnvError(f"calibredb list failed: {stderr}")

    def _probe_server(self) -> str | None:
        base = (self._cfg.server_url or "").rstrip("/")
        if "#" in base:
            probe_url, target = base, base
        else:
            probe_url, target = f"{base}/#-", ""
        result = self._raw(probe_url, "list", "--for-machine", "--fields", "id", "--limit", "1")
        if result.returncode != 0:
            return None
        if target:
            self._server_target = target
            return target
        try:
            libraries = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        first = next(iter(libraries), None) if isinstance(libraries, list) else None
        if not isinstance(first, dict) or "id" not in first:
            return None
        self._server_target = f"{base}/#{first['id']}"
        return self._server_target


    def _target(self) -> str:
        mode = self.resolve_mode()
        if mode == CalibreMode.SERVER:
            target = self._server_target
            if target is None:
                raise CalibreEnvError("server mode resolved without a target URL")
            return target
        return str(self._cfg.library_path)

    def _raw(self, target: str, *args: str) -> subprocess.CompletedProcess[str]:
        cmd = [self._cfg.calibredb_path, "--with-library", target, *args]
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, timeout=self._timeout, check=False
            )
        except FileNotFoundError:
            raise CalibreEnvError(f"calibredb not found: {self._cfg.calibredb_path}") from None
        except subprocess.TimeoutExpired:
            raise CalibreEnvError(
                f"calibredb timed out after {self._timeout}s: {args[0]}"
            ) from None

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = self._raw(self._target(), *args)
        if result.returncode != 0:
            raise CalibreEnvError(
                f"calibredb {args[0]} failed ({result.returncode}): {result.stderr.strip()[:300]}"
            )
        return result

    def list_books(self) -> list[CalibreBook]:
        """All books with formats, languages, tags and #cab_* statuses."""
        base_fields = ("id", "title", "authors", "formats", "languages", "tags")
        result = self._raw(
            self._target(),
            "list", "--for-machine", "--fields", ",".join((*base_fields, *CUSTOM_FIELDS)),
        )
        if result.returncode != 0 and "Unknown field" in (result.stderr or ""):
            # Columns not bootstrapped yet: fall back to bare fields.
            result = self._raw(
                self._target(), "list", "--for-machine", "--fields", ",".join(base_fields)
            )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()[:300]
            raise CalibreEnvError(f"calibredb list failed: {stderr}")
        rows = _json_array(result.stdout)
        return [_row_to_book(row) for row in rows]

    def add_format(self, book_id: int, file: Path, *, replace: bool = True) -> None:
        """Attach a file as a new format (overwrite by default, PLAN.md 5.4)."""
        args = ["add_format", str(book_id), str(file)]
        if not replace:
            args.append("--dont-replace")
        result = self._raw(self._target(), *args)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()[:300]
            raise CalibreEnvError(f"add_format failed: {stderr}")

    def set_custom(self, column: str, book_id: int, value: str | JobStatus) -> None:
        """Set a #cab_* custom column value for one book."""
        self._run("set_custom", column.lstrip("#"), str(book_id), str(value))

    def custom_columns(self) -> list[CustomColumn]:
        """List existing custom columns (best-effort parse of `custom_columns -d`)."""
        result = self._run("custom_columns", "-d")
        columns = []
        for line in result.stdout.splitlines():
            label, _, rest = line.partition(":")
            clean = label.strip().lstrip("#")
            if not clean or not line.strip().startswith("#"):
                continue
            columns.append(CustomColumn(label=clean, name=rest.strip(), datatype=""))
        return columns

    def add_custom_column(
        self, label: str, name: str, datatype: str, display: dict[str, object]
    ) -> None:
        """Create a custom column. Direct access only (calibre must be closed)."""
        args = ["add_custom_column", label, name, datatype]
        if display:
            args += ["--display", json.dumps(display)]
        result = self._raw(self._target(), *args)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()[:300]
            raise CalibreEnvError(f"add_custom_column failed: {stderr}")

    def export(self, book_id: int, fmt: str, to_dir: Path) -> Path:
        """Export one book's file of the given format; return the file path."""
        to_dir.mkdir(parents=True, exist_ok=True)
        before = set(to_dir.iterdir())
        self._run("export", "--formats", fmt, "--to-dir", str(to_dir), str(book_id))
        new_files = sorted(set(to_dir.iterdir()) - before)
        if not new_files:
            new_files = sorted(to_dir.glob(f"*.{fmt.lower()}"))
        if not new_files:
            raise CalibreEnvError(f"export of {fmt} for book {book_id} produced no file")
        return new_files[0]


def _row_to_book(row: dict[str, object]) -> CalibreBook:
    def str_or_empty(key: str) -> str:
        value = row.get(key, "")
        return value if isinstance(value, str) else ""

    return CalibreBook(
        id=int(str(row.get("id", "0"))),
        title=str_or_empty("title"),
        authors=tuple(_as_list(row.get("authors", ""))),
        formats=tuple(f.upper() for f in _as_list(row.get("formats", ""))),
        languages=tuple(
            normalize_language(code) for code in _as_list(row.get("languages", ""))
        ),
        tags=tuple(_as_list(row.get("tags", ""))),
        audiobook_status=str_or_empty(CUSTOM_FIELDS[0]),
        summary_status=str_or_empty(CUSTOM_FIELDS[1]),
    )


def book_status(book: CalibreBook, variant: Variant) -> str:
    """Read the #cab_* status relevant to a variant."""
    match variant:
        case Variant.AUDIOBOOK:
            return book.audiobook_status
        case Variant.SUMMARY:
            return book.summary_status
        case unreachable:
            assert_never(unreachable)
