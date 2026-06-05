"""Dark operations-room Qt stylesheet (spec §7.3)."""

DARK_QSS = """
QWidget { background: #0d120d; color: #cfe0cf; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
QMainWindow, QDialog { background: #0a0e0a; }

QListWidget#sidebar {
    background: #111811; border: none; border-right: 1px solid #2a342a;
    outline: 0; padding-top: 10px; font-size: 14px;
}
QListWidget#sidebar::item { padding: 14px 20px; color: #9fb09f; }
QListWidget#sidebar::item:selected { background: #16241a; color: #36d36b; border-left: 3px solid #36d36b; }
QListWidget#sidebar::item:hover { background: #131c13; }

QLabel#h1 { font-size: 22px; font-weight: 600; color: #e6f3e6; }
QLabel#h2 { font-size: 15px; font-weight: 600; color: #cfe0cf; }
QLabel.muted { color: #7e8c7e; }
QLabel#clock { font-family: 'Consolas', monospace; font-size: 30px; color: #ffb23e; letter-spacing: 4px; }
QLabel.mono, QLineEdit.mono { font-family: 'Consolas', monospace; }

QPushButton {
    background: #1a211a; border: 1px solid #2a342a; border-radius: 8px;
    padding: 8px 16px; color: #cfe0cf;
}
QPushButton:hover { border-color: #36d36b; }
QPushButton:disabled { color: #556155; border-color: #1c241c; }
QPushButton#primary { background: #11351f; border-color: #36d36b; color: #d7ffe6; font-weight: 600; }
QPushButton#danger { border-color: #ff4d4d; color: #ffd0d0; }
QPushButton#ptt { background: #10160f; border: 2px solid #36d36b; border-radius: 12px; font-size: 18px; padding: 24px; }
QPushButton#ptt:pressed { background: #2a0f0f; border-color: #ff4d4d; color: #ff8a8a; }

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #0c110c; border: 1px solid #2a342a; border-radius: 6px; padding: 6px 8px; color: #cfe0cf;
}
QLineEdit:focus, QComboBox:focus { border-color: #36d36b; }

QTableWidget { background: #0c110c; gridline-color: #1c241c; border: 1px solid #2a342a; border-radius: 8px; }
QHeaderView::section { background: #162016; color: #9fb09f; border: none; padding: 6px; }

QGroupBox { border: 1px solid #2a342a; border-radius: 10px; margin-top: 14px; padding: 14px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #7e8c7e; }

QSlider::groove:horizontal { height: 6px; background: #1c241c; border-radius: 3px; }
QSlider::handle:horizontal { width: 16px; background: #36d36b; border-radius: 8px; margin: -6px 0; }
"""
