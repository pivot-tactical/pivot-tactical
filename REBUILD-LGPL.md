# Rebuilding or Substituting the LGPL Component (libsndfile)

PIVOT is a headless server with a browser-based UI, so it bundles **no Qt /
PySide6**. The only weak-copyleft component in the distribution is
**libsndfile**, used via the `soundfile` Python package and distributed under the
**LGPL v2.1**. The LGPL grants you the right to replace it with your own build.
This document explains how, satisfying the project's obligations under spec
§13.4.

## What the LGPL requires here

1. libsndfile is **dynamically linked** (loaded by name at runtime by
   `soundfile`), never statically baked in. PIVOT does not modify its source.
2. The distribution must let you **swap in your own build** of the library.
3. The LGPL licence text and a notice of which component is LGPL ship with the
   binary (see `NOTICE` and `THIRD-PARTY-LICENSES.md`).

## Where the library lives in a build

PIVOT is packaged with PyInstaller in `--onedir` mode so the native libraries
remain separate, replaceable files:

```
PIVOT-Tactical/
├─ PIVOT-Tactical(.exe)
├─ _internal/
│  ├─ _soundfile_data/         # libsndfile shared library
│  │   └─ libsndfile.{dll,so,dylib}
│  └─ ...
└─ ...
```

(If `--onefile` is ever used instead, PyInstaller extracts these same libraries
to a temporary directory at runtime, which still preserves replaceability.)

## Substituting your own libsndfile

1. Obtain or build a libsndfile of the **same major version** the app was built
   against. Build instructions: <https://github.com/libsndfile/libsndfile>.
2. Replace the shared library shipped under `_internal/_soundfile_data/` (or your
   platform's equivalent location) with your build, keeping the same filename.
3. Launch `PIVOT-Tactical`. `soundfile` loads libsndfile by name at import time, so
   a compatible replacement is picked up without rebuilding PIVOT.

## Rebuilding PIVOT from source instead

If you prefer to rebuild the whole application against your own libsndfile:

```bash
pip install -r server/requirements.txt          # installs soundfile/libsndfile
cd frontend && npm install && npm run build && cd ..
pyinstaller packaging/pivot.spec                 # produces dist/PIVOT-Tactical/
```

The produced `dist/PIVOT-Tactical/` tree has the same swappable layout shown above.

## What you may NOT do

- Statically link libsndfile such that it cannot be replaced.
- Strip the LGPL notice from the distribution.

If PIVOT ever ships a **modified** libsndfile (it does not today), those
modifications would be published under the LGPL — see spec §13.4.
