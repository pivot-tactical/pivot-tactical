"""Main window: left-sidebar tab navigation (spec §7.1)."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from pivot.config import Settings
from pivot.gui.tabs.about import AboutTab
from pivot.gui.tabs.band_radios import BandRadiosTab
from pivot.gui.tabs.instructor import InstructorTab
from pivot.gui.tabs.settings import SettingsTab
from pivot.gui.tabs.status import StatusTab
from pivot.runtime.manager import SessionManager
from pivot.version import version_info


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, manager: SessionManager) -> None:
        super().__init__()
        self.settings = settings
        self.manager = manager
        self.setWindowTitle(version_info.title)
        self.resize(1100, 720)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(190)
        self.stack = QStackedWidget()

        # Tabs in spec order (§7.1). The web AAR opens in the browser, so the
        # AAR tab is a simple launcher hosted inside the Status/Instructor flow.
        self._tabs = [
            ("Status", StatusTab(manager, settings)),
            ("Band & Radios", BandRadiosTab(manager)),
            ("Instructor", InstructorTab(manager)),
            ("Settings", SettingsTab(manager, settings)),
            ("About", AboutTab()),
        ]
        for name, widget in self._tabs:
            self.sidebar.addItem(name)
            self.stack.addWidget(widget)

        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

        layout.addWidget(self.sidebar)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        # Refresh live widgets (clock, monitor, status) once a second (§3.1.4, §3.8).
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1000)

    def _refresh(self) -> None:
        for _name, widget in self._tabs:
            if hasattr(widget, "refresh"):
                widget.refresh()
