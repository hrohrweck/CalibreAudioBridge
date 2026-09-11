"""Column bootstrap: creation, idempotency, server-mode refusal."""

from __future__ import annotations

import pytest

from calibreaudiobridge.calibre.client import CalibreClient
from calibreaudiobridge.calibre.columns import REQUIRED_COLUMNS, bootstrap_columns
from calibreaudiobridge.errors import CalibreEnvError

EXPECTED_LABELS = tuple(spec.label for spec in REQUIRED_COLUMNS)


class TestBootstrap_whenColumnsMissing:
    def test_creates_all_four(self, fake_calibre) -> None:
        client = CalibreClient(fake_calibre(custom_columns="missing"))
        report = bootstrap_columns(client)
        assert report.created == EXPECTED_LABELS
        assert report.existing == ()


class TestBootstrap_whenColumnsExist:
    def test_reports_existing(self, fake_calibre) -> None:
        client = CalibreClient(fake_calibre(custom_columns="existing"))
        report = bootstrap_columns(client)
        assert report.created == ()
        assert report.existing == EXPECTED_LABELS


class TestBootstrap_whenServerMode:
    def test_refuses(self, fake_calibre) -> None:
        cfg = fake_calibre(server_url="http://localhost:9999/#lib")
        client = CalibreClient(cfg)
        with pytest.raises(CalibreEnvError, match="direct library access"):
            bootstrap_columns(client)
