"""GUI entry point (spec §7.1)."""

from __future__ import annotations

import sys

from pivot.config import Settings
from pivot.gui.main_window import MainWindow
from pivot.gui.theme import DARK_QSS
from pivot.runtime.manager import SessionManager


def run_gui(settings: Settings, manager: SessionManager) -> int:
    """Show the instructor station and run the Qt event loop.

    Imports PySide6 lazily (the ``gui`` extra). The server runs on a background
    thread and shares ``manager`` with this window (§2.3).
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(DARK_QSS)
    window = MainWindow(settings, manager)
    window.show()
    return app.exec()
