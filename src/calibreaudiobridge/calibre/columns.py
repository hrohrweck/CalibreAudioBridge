"""One-time custom column bootstrap (PLAN.md 5.2). Requires calibre closed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ..errors import CalibreEnvError
from .client import CalibreMode

if TYPE_CHECKING:
    from .client import CalibreClient

ENUM_STATUS_VALUES: Final[tuple[str, ...]] = (
    "pending",
    "processing",
    "done",
    "failed",
    "skipped_drm",
    "skipped_no_text",
    "skipped_manual",
)


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """Definition of one #cab_* column."""

    label: str
    name: str
    datatype: str
    display: dict[str, object]


REQUIRED_COLUMNS: Final[tuple[ColumnSpec, ...]] = (
    ColumnSpec(
        "cab_audiobook_status",
        "Audiobook Status (CalibreAudioBridge)",
        "enumeration",
        {"enum_values": list(ENUM_STATUS_VALUES)},
    ),
    ColumnSpec(
        "cab_summary_status",
        "Summary Status (CalibreAudioBridge)",
        "enumeration",
        {"enum_values": list(ENUM_STATUS_VALUES)},
    ),
    ColumnSpec(
        "cab_last_run",
        "Audio Last Run (CalibreAudioBridge)",
        "datetime",
        {},
    ),
    ColumnSpec(
        "cab_details",
        "Audio Details (CalibreAudioBridge)",
        "comments",
        {},
    ),
)


@dataclass(frozen=True, slots=True)
class BootstrapReport:
    """Outcome of a bootstrap run."""

    created: tuple[str, ...]
    existing: tuple[str, ...]


def bootstrap_columns(client: CalibreClient) -> BootstrapReport:
    """Create missing #cab_* columns; idempotent. Direct access only."""
    if client.resolve_mode() == CalibreMode.SERVER:
        raise CalibreEnvError(
            "add_custom_column needs direct library access: drop calibre.server_url "
            "for this run and close the calibre GUI"
        )
    created: list[str] = []
    existing: list[str] = []
    for spec in REQUIRED_COLUMNS:
        try:
            client.add_custom_column(spec.label, spec.name, spec.datatype, spec.display)
            created.append(spec.label)
        except CalibreEnvError as exc:
            if "exist" in str(exc).lower():
                existing.append(spec.label)
            else:
                raise
    return BootstrapReport(created=tuple(created), existing=tuple(existing))
