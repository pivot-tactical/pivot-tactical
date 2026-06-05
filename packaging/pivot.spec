# PyInstaller spec for PIVOT (spec §9.1, §13.4).
#
# Cross-platform: the same spec builds the Windows (.exe) and Linux bundles; the
# release workflow runs it on each OS and packages dist/PIVOT-Tactical/ as a
# win64 .zip or a linux-x86_64 .tar.gz.
#
# Built in --onedir mode so the one LGPL component, libsndfile (LGPL-2.1, via
# soundfile), stays a separate, replaceable shared library — satisfying the LGPL
# relink obligation (§13.4, see REBUILD-LGPL.md). The server is headless, so no
# Qt/PySide6 is bundled. Explicit hidden imports cover faster-whisper,
# CTranslate2, PyAV (av) and aiortc native deps (§9.1, §10 risk register). The
# built React frontend (frontend/dist) and the legal files are bundled as data
# (at the bundle root, found via sys._MEIPASS) so all attribution travels with
# the binary (§13.8).
#
#   Build:  pyinstaller packaging/pivot.spec
#   Output: dist/PIVOT-Tactical/PIVOT-Tactical(.exe)

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

REPO = Path(os.getcwd())
SERVER = REPO / "server"
FRONTEND_DIST = REPO / "frontend" / "dist"

hidden = []
for pkg in ("faster_whisper", "ctranslate2", "av", "aiortc", "av.audio", "scipy.signal"):
    hidden += collect_submodules(pkg)

binaries = []
for pkg in ("av", "ctranslate2", "aiortc"):
    binaries += collect_dynamic_libs(pkg)

datas = [
    # Attribution / LGPL notices ship alongside the binary (§13.8).
    (str(REPO / "LICENSE"), "."),
    (str(REPO / "NOTICE"), "."),
    (str(REPO / "THIRD-PARTY-LICENSES.md"), "."),
    (str(REPO / "REBUILD-LGPL.md"), "."),
]
if FRONTEND_DIST.is_dir():
    # Served by FastAPI at the LAN address; located via PIVOT_FRONTEND_DIST or
    # the bundled 'frontend_dist' folder (see pivot.api.app.frontend_dist_dir).
    datas.append((str(FRONTEND_DIST), "frontend_dist"))


a = Analysis(
    [str(SERVER / "pivot" / "__main__.py")],
    pathex=[str(SERVER)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PIVOT-Tactical",
    console=True,  # headless server: log to the console; the UI is in the browser
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="PIVOT-Tactical",  # dist/PIVOT-Tactical/ — onedir keeps libsndfile swappable (§13.4)
)
