"""About tab: version, credits, licence info (spec §7.1)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from pivot.version import version_info


class AboutTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)

        title = QLabel("PIVOT")
        title.setObjectName("h1")
        root.addWidget(title)

        root.addWidget(QLabel("Procedural Interactive Voice Operations Trainer — Tactical"))
        version = QLabel(f"Version {version_info.version}  ·  build {version_info.git_sha[:10]}  ·  {version_info.build_date}")
        version.setProperty("class", "muted")
        root.addWidget(version)

        credits = QLabel(
            "Licensed under the Apache License 2.0.\n\n"
            "Built on open-source components: PySide6 (LGPL-3.0, dynamically "
            "linked — see REBUILD-QT.md), aiortc, faster-whisper / CTranslate2, "
            "FastAPI, numpy, scipy, soundfile, SQLAlchemy, React and Vite.\n\n"
            "See LICENSE, NOTICE and THIRD-PARTY-LICENSES for full attribution. "
            "No GPL/AGPL library is linked into the distributed executable."
        )
        credits.setWordWrap(True)
        credits.setAlignment(Qt.AlignTop)
        root.addWidget(credits)
        root.addStretch(1)
