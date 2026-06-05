# PIVOT Implementation Roadmap

Maps **PIVOT Spec v1.6** to the codebase. Legend:

- ✅ **Done** — implemented and covered by tests
- 🟡 **Wired** — backend/control-plane done and tested; needs a native extra
  (`audio`/`transcription`), hardware, or the browser instructor console to
  exercise end-to-end
- ⬜ **Planned** — designed/scaffolded, not yet built out

> **Architecture note:** the desktop PySide6 GUI from spec §7.1 has been replaced
> by a **headless server + browser-based instructor** (password-authenticated).
> Instructor controls are gated by an instructor token rather than loopback
> (§8.4). Rows that referenced the GUI now map to the instructor console in
> `frontend/`.

## 3. Functional Requirements

| Spec | Area | Status | Where |
|------|------|--------|-------|
| 3.1.1 | Server status, session control, terminal count, clock | ✅ | `api/rest.py` + instructor console (`frontend/`) |
| 3.1.2 | Free tuning, emergent nets, band noise profile | ✅ | `core/bands.py`, `core/radios.py` |
| 3.1.2a | Multiple instructor radios | ✅ | `runtime/manager.py`, `api/rest.py`, `api/ws.py` (instr_*) |
| 3.1.3 | Instructor transmit (select + PTT, labelled INSTRUCTOR) | ✅ / 🟡 | `api/ws.py` instr_ptt_* (timing done; media via router) |
| 3.1.4 | Live terminal monitor | ✅ | `runtime/manager.py::monitor_snapshot`, broadcast over `/ws` |
| 3.1.5 | Scenario controls (noise burst, jamming, atmospheric, curve, kick) | ✅ | `runtime/manager.py`, `api/rest.py::admin_scenario` |
| 3.1.6 | Transcription config (model/compute/lang/threshold/skip) | ✅ | `config.py`, `api/rest.py::admin_update_settings`, `transcription/` |
| 3.2.1 | Login (callsign, no password, persists, dup flagging) | ✅ | `api/rest.py`, `frontend/src/views/Login.tsx` |
| 3.2.2 | Radio panel (tune, mode toggle, signal, PTT, clock, state machine) | ✅ | `frontend/src/views/Radio.tsx` |
| 3.2.3 | Crypto sync behaviour (sync tone, delay, abort) | ✅ | `runtime/manager.py`, `api/ws.py`, frontend |
| 3.2.4 | Receive behaviour (auto play, click/squelch tones) | 🟡 | `dsp/tone.py`, router dispatch |
| 3.4.1 | Reception matrix (permissive cypher receive) | ✅ | `core/crypto.py::single_reception` |
| 3.4.2 | Encrypted hash sound | ✅ | `dsp/hash_gen.py` |
| 3.4.3–3.4.5 | Crypto availability, per-radio persistence, mid-TX changes | ✅ | `core/radios.py`, `runtime/manager.py` |
| 3.4.6 | Simplex operation & collisions (plain / cypher) | ✅ | `core/crypto.py`, `core/radios.py` |
| 3.5.1 | Recording (per-station, pre-DSP, 16-bit/16 kHz WAV, metadata) | ✅ | `audio/recording.py`, `runtime/manager.py` |
| 3.5.2 | Async transcription on clean audio, confidence, amber | ✅ | `transcription/worker.py` |
| 3.5.3 | Event metadata schema | ✅ | `db/models.py::EventRow` |
| 3.6.1–3.6.2 | AAR session list + event timeline | ✅ | `frontend/src/views/AAR.tsx`, `api/rest.py` |
| 3.6.3 | Clean/Dirty + Plain/Cypher playback toggles | ✅ | `audio/render.py`, `api/rest.py::event_audio` |
| 3.6.4 | Export (text / CSV / ZIP) | ✅ | `exporting.py` |
| 3.7 | Version management & updates | ✅ / ⬜ | `updates/manager.py` + `updates/github.py` + `/api/admin/updates/check` (release check, Stable/Include-prereleases channel, auto-update flag — all ✅); platform swap helper ⬜ |
| 3.8 | Time & clock (UTC store, configurable display zone, live) | ✅ | `core/timebase.py`, seven-segment clock in `frontend/` |

## 4. DSP & Audio Processing

| Spec | Area | Status | Where |
|------|------|--------|-------|
| 4.1 | Continuous frequency-dependent profile + anchors | ✅ | `core/bands.py`, `dsp/` |
| 4.1.1 | Bandpass, noise, fading, clicks/squelch, HF QRM/selective | ✅ | `dsp/filters.py`, `noise.py`, `fading.py`, `tone.py` |
| 4.2 | Global atmospheric multiplier + jamming | ✅ | `core/bands.py`, `runtime/manager.py` |
| 4.3 | Crypto sync tone (presets, local only) | ✅ | `dsp/tone.py`, frontend `audio.ts` |
| 4.4 | Encrypted hash generator | ✅ | `dsp/hash_gen.py` |
| 4.5 | Playback re-render | ✅ | `audio/render.py`, `dsp/engine.py` |

## 5. Data Model

| Spec | Area | Status | Where |
|------|------|--------|-------|
| 5.1 | config, band_profile, instructor_radios, radio_state, sessions, events, trainees | ✅ | `db/models.py`, `db/repository.py` |

## 6. API & Real-Time Protocols

| Spec | Area | Status | Where |
|------|------|--------|-------|
| 6.1 | REST endpoints | ✅ | `api/rest.py` |
| 6.2 | WebSocket channels (envelope, state sync, PTT) | ✅ | `api/ws.py` |
| 6.3 | WebRTC audio router (per-listener render, fan-out) | 🟡 | `audio/router.py` (orchestration), `audio/mixer.py` (pure core ✅) |

## 7. User Interface (browser — instructor + trainee)

| Spec | Area | Status | Where |
|------|------|--------|-------|
| 7.1 | Instructor console (radios + PTT, live event log, monitor, scenario, settings, password) — **browser, replaces the §7.1 desktop GUI** | ✅ | `frontend/src/views/InstructorConsole.tsx`, `api/`, `runtime/` |
| 7.2 | Trainee web UI (login + radio) | ✅ | `frontend/src/` |
| 7.3 | Visual style (dark, tactical cues, seven-segment, large PTT) | ✅ | `frontend/src/styles.css` |

## 8–13. Non-functional, build, licensing

| Spec | Area | Status | Where |
|------|------|--------|-------|
| 8.3 | Reliability (flush per event, reconnect, mode preserved) | ✅ | `db/database.py` (WAL), `runtime/manager.py`, frontend reconnect |
| 8.4 | Security (LAN-only; **instructor password + bearer token** instead of loopback-only; checksum verify) | ✅ | `auth.py`, `api/deps.py::require_instructor`, `updates/` |
| 8.6 | Logging | 🟡 | loggers in place; rotating-file config in packaging |
| 9.1–9.4 | Build, distribution, updates, uninstall | 🟡 | `packaging/pivot.spec`, `gen_buildinfo.py`; `release.yml` (tag-driven Windows `.zip` + Linux x86_64 `.tar.gz` to GitHub Releases, SHA-256 sidecars); `prerelease.yml` (per-PR-commit auto-incrementing `-dev.N` prereleases + self-updating PR comment mapping version→commit). Platform-aware asset selection in `updates/manager.py`. |
| 13.6 | Compliance artefacts (LICENSE/NOTICE/THIRD-PARTY/REBUILD-LGPL) | ✅ | repo root |
| 13.7 | Build-time licence verification | ✅ | `tools/licenses.py`, `.github/workflows/ci.yml` |

## Acceptance criteria (§12) coverage in tests

Directly asserted in the suite: emergent nets (#3), tuning cleans up low-HF→UHF
(#4), reception matrix #7–#10, immediate plain / delayed cypher (#11–#12),
plain & cypher collisions (#13–#14), per-station recording incl. unheard/aborted
with audibility (#15), AAR clean/dirty + plain/cypher toggles (#18–#19), UTC
storage + zoned display (#20), ZIP export (#21), release identify/order +
update/downgrade/rollback/offline-import (#22–#24), and the licence policy
(#25–#26).

## Notable follow-ups

- WebRTC media end-to-end (browser ↔ aiortc) and Opus encode tuning (§6.3).
- Platform updater helper (Windows + Linux) performing the staged swap +
  relaunch, and downloading the verified release asset for the running OS (§3.7.5).
- Rotating-file logging configuration and audio-router log (§8.6).
- Streaming (block-based) DSP filters for the live path; the offline/whole-buffer
  chain is shared today (§4, §8.1).
- Full noise-vs-frequency curve editor in the instructor console (§7.1, post-v1
  per §11).
