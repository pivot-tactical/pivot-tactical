"""Instructor tab: transmit panel + scenario controls + live monitor (§7.1.1)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pivot.core.bands import parse_frequency
from pivot.core.timebase import format_clock, utc_now
from pivot.db.config_store import ConfigStore
from pivot.runtime.manager import SessionManager


class InstructorTab(QWidget):
    def __init__(self, manager: SessionManager) -> None:
        super().__init__()
        self.manager = manager
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Instructor Station")
        title.setObjectName("h1")
        header.addWidget(title, 1)
        self.clock = QLabel("00:00:00")
        self.clock.setObjectName("clock")
        header.addWidget(self.clock, 0, Qt.AlignRight)
        root.addLayout(header)

        # --- transmit (§3.1.3) ---
        tx = QGroupBox("Transmit")
        txl = QHBoxLayout(tx)
        self.radio_select = QComboBox()
        txl.addWidget(QLabel("Active radio:"))
        txl.addWidget(self.radio_select, 1)
        self.ptt = QPushButton("PUSH TO TALK  (hold)")
        self.ptt.setObjectName("ptt")
        self.ptt.pressed.connect(self._ptt_down)
        self.ptt.released.connect(self._ptt_up)
        txl.addWidget(self.ptt, 1)
        root.addWidget(tx)

        # --- scenario controls (§3.1.5) ---
        scenario = QGroupBox("Scenario Controls")
        sl = QHBoxLayout(scenario)
        self.jam_low = QLineEdit()
        self.jam_low.setPlaceholderText("Jam from (e.g. 14.2 MHz)")
        self.jam_high = QLineEdit()
        self.jam_high.setPlaceholderText("to (e.g. 14.3 MHz)")
        jam_btn = QPushButton("Toggle Jam")
        jam_btn.clicked.connect(self._toggle_jam)
        burst_btn = QPushButton("Noise Burst")
        burst_btn.clicked.connect(self._noise_burst)
        sl.addWidget(self.jam_low)
        sl.addWidget(self.jam_high)
        sl.addWidget(jam_btn)
        sl.addWidget(burst_btn)
        root.addWidget(scenario)

        # --- live terminal monitor (§3.1.4) ---
        monitor = QGroupBox("Live Terminal Monitor")
        ml = QVBoxLayout(monitor)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Callsign", "Frequency", "Mode", "Status", "Last activity"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        ml.addWidget(self.table)
        kick_row = QHBoxLayout()
        self.kick_id = QLineEdit()
        self.kick_id.setPlaceholderText("trainee_id to kick")
        kick_btn = QPushButton("Kick")
        kick_btn.setObjectName("danger")
        kick_btn.clicked.connect(self._kick)
        kick_row.addStretch(1)
        kick_row.addWidget(self.kick_id)
        kick_row.addWidget(kick_btn)
        ml.addLayout(kick_row)
        root.addWidget(monitor, 1)

        self._jam_on = False
        self._jam_span: tuple[float, float] | None = None
        self.refresh()

    # -- transmit ---------------------------------------------------------- #

    def _active_radio_id(self) -> str | None:
        return self.radio_select.currentData()

    def _ptt_down(self) -> None:
        radio_id = self._active_radio_id()
        if radio_id is None:
            return
        result = self.manager.ptt_start(radio_id)
        if result["sync_applies"]:
            QTimer.singleShot(result["sync_delay_ms"], lambda: self.manager.ptt_sync_complete(radio_id))

    def _ptt_up(self) -> None:
        radio_id = self._active_radio_id()
        if radio_id is None:
            return
        radio = self.manager.registry.get(radio_id)
        if radio is not None and radio.syncing:
            self.manager.ptt_abort(radio_id)
        else:
            self.manager.ptt_end(radio_id)

    # -- scenario ---------------------------------------------------------- #

    def _toggle_jam(self) -> None:
        try:
            low = parse_frequency(self.jam_low.text() or "14.2")
            high = parse_frequency(self.jam_high.text() or "14.3")
        except ValueError:
            return
        self._jam_on = not self._jam_on
        self.manager.toggle_jamming(low, high, on=self._jam_on)

    def _noise_burst(self) -> None:
        try:
            low = parse_frequency(self.jam_low.text() or "14.2")
            high = parse_frequency(self.jam_high.text() or "14.3")
        except ValueError:
            return
        self.manager.inject_noise_burst(low, high)

    def _kick(self) -> None:
        tid = self.kick_id.text().strip()
        if tid:
            self.manager.kick(tid)
            self.kick_id.clear()

    # -- refresh ----------------------------------------------------------- #

    def refresh(self) -> None:
        with self.manager.db.session() as s:
            tz = ConfigStore(s).display_timezone()
        self.clock.setText(format_clock(utc_now(), tz))

        # Rebuild the active-radio selector from current instructor radios.
        current = self.radio_select.currentData()
        self.radio_select.blockSignals(True)
        self.radio_select.clear()
        for r in self.manager.instructor_radios():
            self.radio_select.addItem(f"{r['name']} — {r['frequency']} [{r['mode']}]", r["radio_id"])
        idx = self.radio_select.findData(current)
        if idx >= 0:
            self.radio_select.setCurrentIndex(idx)
        self.radio_select.blockSignals(False)

        snapshot = self.manager.monitor_snapshot()
        trainees = [t for t in snapshot if not t["is_instructor"]]
        self.table.setRowCount(len(trainees))
        for row, t in enumerate(trainees):
            self.table.setItem(row, 0, QTableWidgetItem(t["name"]))
            self.table.setItem(row, 1, QTableWidgetItem(f"{t['frequency']} ({t['band_region']})"))
            self.table.setItem(row, 2, QTableWidgetItem(t["mode"]))
            self.table.setItem(row, 3, QTableWidgetItem(t["status"]))
            self.table.setItem(row, 4, QTableWidgetItem(t["last_activity"][11:19]))
