# Rebuilding or Substituting Qt / PySide6 (LGPL v3 relink rights)

PIVOT's instructor GUI uses **PySide6 (Qt for Python)**, which is distributed
under the **LGPL v3**. libsndfile (used via `soundfile`) is **LGPL v2.1**. The
LGPL grants you the right to replace these components with your own versions.
This document explains how to exercise that right, satisfying the project's
obligations under spec §13.4.

## What the LGPL requires here

1. PySide6/Qt and libsndfile are **dynamically linked**, never statically baked
   in. PIVOT does not modify their source.
2. The distribution must let you **swap in your own build** of these libraries.
3. The LGPL licence text and a notice of which components are LGPL ship with the
   binary (see `NOTICE` and `THIRD-PARTY-LICENSES.md`).

## Where the libraries live in a build

PIVOT is packaged with PyInstaller in `--onedir` mode (preferred) so that the Qt
shared libraries remain separate, replaceable files:

```
RadioTrainer/
├─ RadioTrainer.exe
├─ _internal/
│  ├─ PySide6/
│  │  ├─ Qt6Core.dll
│  │  ├─ Qt6Gui.dll
│  │  ├─ Qt6Widgets.dll
│  │  └─ ... (other Qt6*.dll, plugins/, qml/)
│  ├─ shiboken6/
│  └─ _soundfile_data/        # libsndfile native library
└─ ...
```

If `--onefile` is used instead, PyInstaller extracts these same DLLs to a
temporary directory at runtime, which still preserves replaceability.

## Substituting your own Qt build (PySide6)

1. Obtain or build a PySide6/Qt6 of the **same major.minor version** the app was
   built against (printed in **About → Qt version**, and pinned in
   `server/requirements.txt`).
2. Build Qt from source if you wish: <https://doc.qt.io/qt-6/build-sources.html>,
   then build PySide6 against it:
   <https://doc.qt.io/qtforpython-6/gettingstarted/index.html>.
3. Replace the `Qt6*.dll` files (and `plugins/`, `qml/` as needed) in
   `_internal/PySide6/` with your built equivalents, keeping the same filenames.
4. Launch `RadioTrainer.exe`. The application binds to Qt by name at load time,
   so a compatible replacement is picked up without rebuilding PIVOT.

## Substituting libsndfile

Replace the `libsndfile` shared library shipped under `_internal/_soundfile_data/`
(or your platform's equivalent) with your own build of the same major version.
`soundfile` loads it by name at import time.

## Rebuilding PIVOT from source instead

If you prefer to rebuild the whole application against your own Qt:

```bash
# from the repository root
python -m venv .venv && . .venv/bin/activate        # (Windows: .venv\Scripts\activate)
pip install -r server/requirements.txt              # installs your chosen PySide6
cd frontend && npm ci && npm run build && cd ..
pyinstaller packaging/pivot.spec                    # produces dist/RadioTrainer/
```

The produced `dist/RadioTrainer/` tree has the same swappable layout shown above.

## What you may NOT do

- Statically link Qt into `RadioTrainer.exe` such that it cannot be replaced.
- Strip the LGPL notices from the distribution.

If PIVOT ever ships a **modified** Qt/PySide6 (it does not today), those
modifications would be published under the LGPL — see spec §13.4.
