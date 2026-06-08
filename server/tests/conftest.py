"""Shared pytest fixtures."""

from pathlib import Path

import pytest

from pivot.config import Settings
from pivot.db.database import Database


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # Ambient noise off by default in tests: the broadcaster would otherwise
    # interleave binary noise frames with the JSON the WebSocket tests read. The
    # idle-noise policy itself is covered directly in test_idle_noise.py.
    return Settings(
        data_dir=tmp_path / "data",
        versions_dir=tmp_path / "versions",  # keep rollback/retain tests off cwd
        ambient_noise=False,
    )


@pytest.fixture
def database(settings: Settings) -> Database:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    db.initialise()
    return db
