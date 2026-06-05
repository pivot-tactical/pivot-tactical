"""Status tab: server health, connection info, session control, clock (§3.1.1)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pivot.config import Settings
from pivot.core.timebase import format_clock, utc_now
from pivot.db.config_store import ConfigStore
from pivot.runtime.manager import SessionManager


def _lan_ip() -> str:
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


class StatusTab(QWidget):
    def __init__(self, manager: SessionManager, settings: Settings) -> None:
        super().__init__()
        self.manager = manager
        self.settings = settings

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel("Server Status")
        title.setObjectName("h1")
        root.addWidget(title)

        self.address = QLabel()
        self.address.setObjectName("h2")
        root.addWidget(self.address)

        self.server_state = QLabel("FastAPI: running   ·   Audio router: idle")
        self.server_state.setProperty("class", "muted")
        root.addWidget(self.server_state)

        self.clock = QLabel("00:00:00")
        self.clock.setObjectName("clock")
        self.clock.setAlignment(Qt.AlignLeft)
        root.addWidget(self.clock)

        # Session control (§3.1.1).
        session_row = QHBoxLayout()
        self.session_name = QLineEdit()
        self.session_name.setPlaceholderText("Session name (e.g. Exercise BRAVO)")
        self.start_btn = QPushButton("Start Session")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self._toggle_session)
        session_row.addWidget(self.session_name, 1)
        session_row.addWidget(self.start_btn)
        root.addLayout(session_row)

        self.session_state = QLabel("No active session.")
        self.session_state.setProperty("class", "muted")
        root.addWidget(self.session_state)

        self.terminal_count = QLabel("Connected terminals: 0")
        root.addWidget(self.terminal_count)

        root.addStretch(1)
        self.refresh()

    def _toggle_session(self) -> None:
        if self.manager.session_active:
            self.manager.end_session()
        else:
            name = self.session_name.text().strip() or "Untitled Exercise"
            self.manager.start_session(name)
        self.refresh()

    def refresh(self) -> None:
        self.address.setText(
            f"LAN address for trainees:  http://{_lan_ip()}:{self.settings.port}"
        )
        with self.manager.db.session() as s:
            tz = ConfigStore(s).display_timezone()
        self.clock.setText(format_clock(utc_now(), tz))
        active = self.manager.session_active
        self.start_btn.setText("Stop Session" if active else "Start Session")
        self.session_name.setEnabled(not active)
        if active:
            self.session_state.setText(f"Active session: {self.manager.current_session_id}")
        else:
            self.session_state.setText("No active session.")
        self.terminal_count.setText(f"Connected terminals: {len(self.manager.terminals)}")
