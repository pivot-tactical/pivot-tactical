"""Band & Radios tab: conditions + instructor radios (spec §7.1, §3.1.2/§3.1.2a)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pivot.core.crypto import RadioMode
from pivot.runtime.manager import SessionManager


class BandRadiosTab(QWidget):
    def __init__(self, manager: SessionManager) -> None:
        super().__init__()
        self.manager = manager
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel("Band & Radios")
        title.setObjectName("h1")
        root.addWidget(title)

        # --- global conditions (§3.1.2, §4.2) ---
        conditions = QGroupBox("Global Conditions")
        clayout = QVBoxLayout(conditions)

        atmo_row = QHBoxLayout()
        atmo_row.addWidget(QLabel("Atmospheric severity"))
        self.atmo = QSlider(Qt.Horizontal)
        self.atmo.setRange(25, 300)  # 0.25x .. 3.0x
        self.atmo.setValue(100)
        self.atmo.valueChanged.connect(self._atmo_changed)
        self.atmo_label = QLabel("1.00x")
        atmo_row.addWidget(self.atmo, 1)
        atmo_row.addWidget(self.atmo_label)
        clayout.addLayout(atmo_row)

        self.crypto_enabled = QCheckBox("Crypto available to all radios")
        self.crypto_enabled.setChecked(self.manager.band_profile.crypto_enabled)
        self.crypto_enabled.toggled.connect(self.manager.set_crypto_enabled)
        clayout.addWidget(self.crypto_enabled)

        self.curve_summary = QLabel()
        self.curve_summary.setProperty("class", "muted")
        clayout.addWidget(self.curve_summary)
        root.addWidget(conditions)

        # --- instructor radios (§3.1.2a) ---
        radios = QGroupBox("Instructor Radios")
        rlayout = QVBoxLayout(radios)
        add_row = QHBoxLayout()
        self.new_freq = QLineEdit()
        self.new_freq.setPlaceholderText("Frequency e.g. 30.000 MHz")
        add_btn = QPushButton("Add Radio")
        add_btn.setObjectName("primary")
        add_btn.clicked.connect(self._add_radio)
        add_row.addWidget(self.new_freq, 1)
        add_row.addWidget(add_btn)
        rlayout.addLayout(add_row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Radio", "Frequency", "Region", "Mode", ""])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        rlayout.addWidget(self.table)
        root.addWidget(radios, 1)

        self.refresh()

    def _atmo_changed(self, value: int) -> None:
        mult = value / 100.0
        self.atmo_label.setText(f"{mult:.2f}x")
        self.manager.set_atmospheric(mult)

    def _add_radio(self) -> None:
        freq = self.new_freq.text().strip() or "30.000 MHz"
        self.manager.add_instructor_radio(frequency=freq)
        self.new_freq.clear()
        self.refresh()

    def _toggle_mode(self, radio_id: str) -> None:
        radio = self.manager.registry.get(radio_id)
        if radio is not None:
            self.manager.set_mode(radio_id, radio.mode.toggled())
            self.refresh()

    def _remove(self, radio_id: str) -> None:
        self.manager.remove_instructor_radio(radio_id)
        self.refresh()

    def refresh(self) -> None:
        bp = self.manager.band_profile
        c_low = bp.conditions_at(2e6)
        c_uhf = bp.conditions_at(440e6)
        self.curve_summary.setText(
            f"Noise vs frequency: low HF ≈ {c_low.snr_db:.0f} dB SNR  →  "
            f"UHF ≈ {c_uhf.snr_db:.0f} dB SNR  (continuous, interpolated)"
        )
        radios = self.manager.instructor_radios()
        self.table.setRowCount(len(radios))
        for row, r in enumerate(radios):
            self.table.setItem(row, 0, QTableWidgetItem(r["name"]))
            self.table.setItem(row, 1, QTableWidgetItem(r["frequency"]))
            self.table.setItem(row, 2, QTableWidgetItem(r["band_region"]))
            mode_btn = QPushButton("🔒 Cypher" if r["mode"] == RadioMode.CYPHER.value else "◌ Plain")
            mode_btn.clicked.connect(lambda _=False, rid=r["radio_id"]: self._toggle_mode(rid))
            self.table.setCellWidget(row, 3, mode_btn)
            rm = QPushButton("Remove")
            rm.setObjectName("danger")
            rm.clicked.connect(lambda _=False, rid=r["radio_id"]: self._remove(rid))
            self.table.setCellWidget(row, 4, rm)
