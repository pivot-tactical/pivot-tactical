"""GUI smoke test (spec §7.1).

Skipped unless PySide6 (the ``gui`` extra) is installed. When present, it builds
the main window headlessly (offscreen) to catch wiring errors. The GUI shares the
live SessionManager with the server (§2.3), so this also verifies the tabs can
read manager state without a running server.
"""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pivot.runtime.manager import SessionManager  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_builds_and_refreshes(qapp, database, settings):
    from pivot.gui.main_window import MainWindow

    manager = SessionManager(database, settings)
    manager.add_instructor_radio("Radio 1", "30.000 MHz")
    win = MainWindow(settings, manager)
    # Exercise the per-second refresh across every tab.
    win._refresh()
    assert win.sidebar.count() == win.stack.count() == 5
    win.close()


def test_status_tab_session_toggle(qapp, database, settings):
    from pivot.gui.tabs.status import StatusTab

    manager = SessionManager(database, settings)
    tab = StatusTab(manager, settings)
    tab.session_name.setText("EX")
    tab._toggle_session()
    assert manager.session_active is True
    tab._toggle_session()
    assert manager.session_active is False
