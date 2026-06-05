# PIVOT — Procedural Interactive Voice Operations Trainer (Tactical)

A self-hosted, **LAN-only** software system that simulates VHF/UHF/HF radio voice
communications for training military and emergency-services personnel in voice
procedure, net discipline, prowords and tactical message formats (SITREP,
SALUTE, MEDEVAC, …) — **without real radio equipment or live spectrum**.

The server runs as a single application on one machine. The operator is the
**instructor**, controlling the exercise from a desktop GUI. Trainees connect
from any device with a **web browser** over the LAN — zero install. Radios are
free-tuning across the whole HF/VHF/UHF range; nets are *emergent* (anyone on the
same frequency is on the same net, exactly like real radios), and tuning upward
audibly cleans up from noisy low HF to near-clean UHF.

Built per **PIVOT Spec v1.6**. Licensed under **Apache-2.0**.

> **Status:** active development. The core domain logic, DSP engine, data layer,
> live runtime, REST + WebSocket API, transcription worker, update manager, the
> React trainee UI and the PySide6 instructor GUI are implemented with a
> 156-test suite. The WebRTC media plane (aiortc) and the Windows packaged build
> are scaffolded and wired but require the native `audio`/`gui` extras to run.
> See [`ROADMAP.md`](ROADMAP.md) for a section-by-section status map.

---

## Architecture

```
Server machine                          Trainee terminals (any browser)
└─ pivot (single process)               └─ http://[server-ip]:8080
   ├─ PySide6 GUI (instructor)             ├─ Login (callsign)
   ├─ FastAPI (HTTP + WebSocket)           ├─ Radio panel (mic via WebRTC + PTT)
   ├─ WebRTC audio router (aiortc)         └─ AAR (after-action review)
   ├─ DSP engine (numpy/scipy)
   ├─ faster-whisper (transcription)
   └─ SQLite (config + sessions + events)
```

* **Per-listener audio** is the defining constraint: a single transmission is
  heard differently by different receivers on the same frequency (clear voice,
  encrypted hash, or a collision render), so the server renders per listener and
  WebRTC carries it to the browser. See spec Appendix A.
* **Continuous band model:** one DSP chain whose noise/fading vary continuously
  with the tuned frequency, plus a global atmospheric multiplier and instructor
  jamming.
* **Crypto is per-radio** (Plain/Cypher), persists across retuning, and never
  auto-resets. Permissive cypher receive: a cypher set decodes plain too.

## Repository layout

```
server/            Python backend (pip-installable package `pivot`)
  pivot/
    core/          Pure domain logic: bands, crypto matrix, radios, time
    dsp/           numpy/scipy DSP engine (noise, fading, hash, tones)
    db/            SQLAlchemy models, migrations, repository, config store
    runtime/       SessionManager — the live control plane
    audio/         recording tap, AAR re-render, WebRTC router + mixer
    transcription/ async faster-whisper worker
    updates/       version management, rollback, offline import
    api/           FastAPI REST + WebSocket app
    gui/           PySide6 instructor station
    tools/         build-time licence verification
  tests/           156-test suite
frontend/          React + Vite + TypeScript trainee UI
packaging/         PyInstaller spec + build-info generator
```

## Quickstart (development)

**Backend** (core install needs no native media/GUI deps):

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ./server
pip install pytest pytest-asyncio httpx ruff     # dev tools

cd server && python -m pytest -q                  # run the test suite
python -m pivot --headless                        # run the server only
```

Open `http://localhost:8080`. For the full experience add the native extras:

```bash
pip install -e "./server[audio,transcription,gui]"   # aiortc, faster-whisper, PySide6
python -m pivot                                       # GUI + server
```

**Frontend** (the trainee UI):

```bash
cd frontend
npm install
npm run dev        # dev server on :5173, proxies /api + /ws to :8080
npm run build      # production build -> frontend/dist (served by FastAPI)
```

## Packaging (Windows executable)

```bash
cd frontend && npm ci && npm run build && cd ..
python packaging/gen_buildinfo.py                # embed git SHA + build date
pyinstaller packaging/pivot.spec                 # -> dist/RadioTrainer/
```

`--onedir` mode keeps the Qt/PySide6 libraries as separate, replaceable files,
satisfying the LGPL relink obligation (see below). Distribute the ZIP with
`LICENSE`, `NOTICE` and `THIRD-PARTY-LICENSES.md` alongside the binary. The
SQLite database, recordings and settings live in a data directory **outside** the
swappable application folder so they survive any update or rollback.

## Testing & CI

`python -m pytest` runs the full suite (domain logic, DSP behaviour, data layer,
runtime lifecycle, REST + WebSocket, transcription, updates, mixer, licence
policy). CI additionally runs `ruff`, builds the frontend, and enforces the
licence policy — the build **fails** on any GPL/AGPL strong-copyleft runtime
dependency (`python -m pivot.tools.licenses --check`).

---

## Licensing & open-source compliance

PIVOT is released under the **Apache License 2.0** (see [`LICENSE`](LICENSE)). A
core project requirement is that the project licence does not contravene any
upstream dependency licence.

* [`NOTICE`](NOTICE) — attribution for Apache-2.0 and bundled components.
* [`THIRD-PARTY-LICENSES.md`](THIRD-PARTY-LICENSES.md) — full dependency licence
  inventory and the allow/deny policy (enforced in CI).
* [`REBUILD-QT.md`](REBUILD-QT.md) — how to substitute or rebuild the LGPL
  components, satisfying the relink obligation.

**Weak-copyleft (LGPL) components, dynamically linked and replaceable:**

* **PySide6 (Qt for Python)** — LGPL-3.0. The GUI binding; kept as separate
  shared libraries so users can swap their own build (see `REBUILD-QT.md`).
* **libsndfile** (via `soundfile`) — LGPL-2.1. Loaded dynamically by name.

No GPL/AGPL strong-copyleft library is linked into the distributed executable.
PyInstaller (GPL with a linking exception) is a build tool only and is not
redistributed inside the binary. The DSP engine is implemented entirely on
numpy + scipy (BSD), keeping the audio path fully permissive.
