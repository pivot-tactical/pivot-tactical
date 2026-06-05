# Third-Party Licences

PIVOT is licensed under the Apache License 2.0 (see `LICENSE`). It bundles or
links against the third-party components listed below. This file is the
canonical inventory referenced by the spec (§13.6) and is verified in CI
(§13.7). It is regenerated from the locked dependency set; do not edit the
generated section by hand.

> **Regeneration:** `python -m pivot.tools.licenses` (Python) and
> `npx license-checker --production` (frontend) produce the machine-readable
> inventory. CI fails the build if this file is stale or if any dependency
> reports a denylisted (GPL/AGPL strong-copyleft) licence that is linked into
> the distributed executable.

## Licence policy

| Class | Licences | Linked into the distributed `.exe`? |
|-------|----------|-------------------------------------|
| Allowed (permissive) | MIT, BSD-2/3-Clause, Apache-2.0, ISC, Python-2.0 | Yes |
| Allowed (weak copyleft, dynamic link only) | LGPL-2.1, LGPL-3.0 | Yes — dynamically linked, replaceable (see `REBUILD-QT.md`) |
| Allowed (build tool only) | GPL-2.0-with-linking-exception (PyInstaller) | No — build tool; exception permits permissive output |
| **Denied** | GPL-2.0, GPL-3.0, AGPL-3.0 (strong copyleft) | **Never** |

## Runtime dependency inventory

| Component | Version (pin) | Licence | Type | Obligation |
|-----------|---------------|---------|------|------------|
| PySide6 (Qt for Python) | 6.x | LGPL-3.0 | Weak copyleft | Dynamic link + relink ability — see `REBUILD-QT.md` |
| aiortc | 1.x | BSD-3-Clause | Permissive | Attribution |
| PyAV (`av`) | 12.x | BSD-3-Clause | Permissive | Attribution |
| faster-whisper | 1.x | MIT | Permissive | Attribution |
| CTranslate2 | 4.x | MIT | Permissive | Attribution |
| FastAPI | 0.11x | MIT | Permissive | Attribution |
| Starlette | 0.x | BSD-3-Clause | Permissive | Attribution |
| Uvicorn | 0.x | BSD-3-Clause | Permissive | Attribution |
| numpy | 2.x | BSD-3-Clause | Permissive | Attribution |
| scipy | 1.x | BSD-3-Clause | Permissive | Attribution |
| soundfile | 0.x | BSD-3-Clause | Permissive | Attribution |
| libsndfile (native, via soundfile) | 1.2.x | LGPL-2.1 | Weak copyleft | Dynamic link — replaceable |
| SQLAlchemy | 2.x | MIT | Permissive | Attribution |
| pydantic / pydantic-settings | 2.x | MIT | Permissive | Attribution |
| React | 18.x | MIT | Permissive | Attribution |
| Vite | 5.x | MIT | Permissive | Attribution |

## Build-tooling inventory (not linked into the distributed binary)

| Component | Licence | Note |
|-----------|---------|------|
| PyInstaller | GPL-2.0-WITH-exception | Linking exception permits distributing the produced executable under the project's own terms. PyInstaller itself is not redistributed inside the binary. |
| pytest | MIT | Test only |
| ruff | MIT | Lint only |

## Model weights

The converted CTranslate2 Whisper models distributed by SYSTRAN are MIT; the
underlying OpenAI Whisper checkpoints are MIT. Model weights are downloaded on
first run or bundled; no model-weight licence conflict exists (spec §13.3).

## Full licence texts

The complete, verbatim licence texts for every component above are collected in
`legal/` at build time and shipped alongside the executable in the distribution
ZIP (spec §13.8). The Apache-2.0 text is in `LICENSE`; the LGPL-3.0 text is in
`legal/LGPL-3.0.txt` (added by the licence-collection step).

_Last generated: pending first CI run. The list above is authored to match the
spec dependency inventory (§13.3); exact pinned versions are written here by the
generator once `requirements.lock` / `package-lock.json` exist._
