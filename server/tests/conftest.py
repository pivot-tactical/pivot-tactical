"""Shared pytest fixtures."""

from pathlib import Path

import pytest

from pivot.config import Settings
from pivot.db.database import Database


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def database(settings: Settings) -> Database:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    db.initialise()
    return db
