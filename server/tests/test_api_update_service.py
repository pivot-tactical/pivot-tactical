from unittest.mock import MagicMock

from pivot.api.app import _start_update_service
from pivot.config import Settings
from pivot.db.config_store import ConfigStore
from pivot.db.database import Database
from pivot.runtime.manager import SessionManager
from pivot.updates.service import UpdateService


def test_start_update_service_wiring(tmp_path, monkeypatch):
    # Setup mocks
    settings = Settings(data_dir=tmp_path / "data", versions_dir=tmp_path / "versions")
    settings.ensure_dirs()

    db = Database(settings.db_path)
    db.initialise()

    # Pre-populate some config so config_provider() returns it
    with db.session() as s:
        store = ConfigStore(s)
        store.set("update_channel", "beta")

    manager = SessionManager(db, settings)

    # Track broadcasts
    broadcasts = []
    manager.broadcast = MagicMock(side_effect=lambda ev, data: broadcasts.append((ev, data)))

    # We also need to monkeypatch github.fetch_releases and UpdateService.start
    import pivot.updates.github as github

    mock_fetch = MagicMock(return_value=[])
    monkeypatch.setattr(github, "fetch_releases", mock_fetch)

    mock_start = MagicMock()
    monkeypatch.setattr(UpdateService, "start", mock_start)

    # Act
    service = _start_update_service(manager, settings)

    # Assert wiring
    assert service is not None
    assert isinstance(service, UpdateService)
    assert manager.update_service == service
    mock_start.assert_called_once()

    # Test config_provider wiring
    cfg = service._config()
    assert isinstance(cfg, dict)
    assert cfg.get("update_channel") == "beta"

    # Test session_active wiring
    monkeypatch.setattr(SessionManager, "session_active", True)
    assert service._session_active() is True

    # Test releases_provider wiring
    releases = service._fetch("foo/bar", "secret")
    mock_fetch.assert_called_once_with("foo/bar", "secret")
    assert releases == []

    # Test updater_kind wiring
    assert service._updater_kind() == "staged"
