"""Settings tab: transcription, timezone, crypto, and update controls (§7.1, §3.7)."""

from __future__ import annotations

from zoneinfo import available_timezones

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pivot.config import Settings
from pivot.db.config_store import ConfigStore
from pivot.runtime.manager import SessionManager
from pivot.version import version_info

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
COMPUTE_TYPES = ["int8", "int8_float16", "float16"]


class SettingsTab(QWidget):
    def __init__(self, manager: SessionManager, settings: Settings) -> None:
        super().__init__()
        self.manager = manager
        self.settings = settings
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel("Settings")
        title.setObjectName("h1")
        root.addWidget(title)

        with manager.db.session() as s:
            cfg = ConfigStore(s).all()

        # --- transcription (§3.1.6) ---
        trans = QGroupBox("Transcription (faster-whisper)")
        tform = QFormLayout(trans)
        self.model = QComboBox()
        self.model.addItems(WHISPER_MODELS)
        self.model.setCurrentText(cfg.get("whisper_model", "small"))
        self.compute = QComboBox()
        self.compute.addItems(COMPUTE_TYPES)
        self.compute.setCurrentText(cfg.get("whisper_compute_type", "int8"))
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.0, 1.0)
        self.threshold.setSingleStep(0.05)
        self.threshold.setValue(float(cfg.get("transcription_confidence_threshold", 0.80)))
        self.skip = QDoubleSpinBox()
        self.skip.setRange(0.0, 5.0)
        self.skip.setSingleStep(0.1)
        self.skip.setValue(float(cfg.get("transcription_skip_under_seconds", 0.5)))
        tform.addRow("Model size", self.model)
        tform.addRow("Compute type", self.compute)
        tform.addRow("Amber confidence threshold", self.threshold)
        tform.addRow("Skip events shorter than (s)", self.skip)
        root.addWidget(trans)

        # --- time & crypto (§3.8, §4.3) ---
        misc = QGroupBox("Time & Crypto")
        mform = QFormLayout(misc)
        self.timezone = QComboBox()
        zones = sorted(available_timezones())
        self.timezone.addItems(zones)
        self.timezone.setCurrentText(cfg.get("display_timezone", "UTC"))
        self.crypto_delay = QSpinBox()
        self.crypto_delay.setRange(0, 5000)
        self.crypto_delay.setSingleStep(100)
        self.crypto_delay.setValue(int(cfg.get("crypto_delay_ms", 1500)))
        mform.addRow("Display timezone", self.timezone)
        mform.addRow("Crypto sync delay (ms)", self.crypto_delay)
        root.addWidget(misc)

        save = QPushButton("Save Settings")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        root.addWidget(save)

        # --- updates (§3.7) ---
        updates = QGroupBox("Version & Updates")
        ul = QVBoxLayout(updates)
        ul.addWidget(QLabel(f"Current version: {version_info.title}"))
        btn_row = QHBoxLayout()
        for label in ("Check for Updates", "Roll Back to Previous", "Import Update Package…"):
            b = QPushButton(label)
            b.setEnabled(False)  # wired to UpdateManager in the update dialog (§3.7.4)
            btn_row.addWidget(b)
        ul.addLayout(btn_row)
        note = QLabel(
            "Updates are out-of-band and never applied during an active session. "
            "Air-gapped sites use offline import (spec §3.7)."
        )
        note.setProperty("class", "muted")
        note.setWordWrap(True)
        ul.addWidget(note)
        root.addWidget(updates)

        root.addStretch(1)

    def _save(self) -> None:
        with self.manager.db.session() as s:
            cfg = ConfigStore(s)
            cfg.set("whisper_model", self.model.currentText())
            cfg.set("whisper_compute_type", self.compute.currentText())
            cfg.set("transcription_confidence_threshold", self.threshold.value())
            cfg.set("transcription_skip_under_seconds", self.skip.value())
            cfg.set("crypto_delay_ms", self.crypto_delay.value())
        # Timezone changes broadcast to all clocks live (§3.8).
        self.manager.set_display_timezone(self.timezone.currentText())
        self.manager.band_profile.crypto_delay_ms = self.crypto_delay.value()
