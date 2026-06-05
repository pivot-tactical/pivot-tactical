# PIVOT Implementation Roadmap

Maps **PIVOT Spec v1.6** to the codebase. Legend:

- ✅ **Done** — implemented and covered by tests
- 🟡 **Wired** — implemented but needs a native extra (`audio`/`gui`) or hardware
  to exercise end-to-end; structure and control-plane done, media/UI not headless-testable
- ⬜ **Planned** — designed/scaffolded, not yet built out

## 3. Functional Requirements

| Spec | Area | Status | Where |
|------|------|--------|-------|
| 3.1.1 | Server status, session control, terminal count, clock | ✅ | `gui/tabs/status.py`, `api/rest.py` |
| 3.1.2 | Free tuning, emergent nets, band noise profile | ✅ | `core/bands.py`, `core/radios.py` |
| 3.1.2a | Multiple instructor radios | ✅ | `runtime/manager.py`, `gui/tabs/band_radios.py` |
| 3.1.3 | Instructor transmit (select + PTT, labelled INSTRUCTOR) | ✅ / 🟡 | `gui/tabs/instructor.py` (PTT timing done; media via router) |
| 3.1.4 | Live terminal monitor | ✅ | `runtime/manager.py::monitor_snapshot`, `gui/tabs/instructor.py` |
| 3.1.5 | Scenario controls (noise burst, jamming, atmospheric, curve, kick) | ✅ | `runtime/manager.py`, `api/rest.py::admin_scenario` |
| 3.1.6 | Transcription config (model/compute/lang/threshold/skip) | ✅ | `config.py`, `gui/tabs/settings.py`, `transcription/` |
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
| 3.7 | Version management & updates | ✅ / ⬜ | `updates/manager.py` (policy ✅; Windows swap helper ⬜) |
| 3.8 | Time & clock (UTC store, configurable display zone, live) | ✅ | `core/timebase.py`, clocks in GUI + frontend |

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

## 7. User Interface

| Spec | Area | Status | Where |
|------|------|--------|-------|
| 7.1 | Instructor window + tabs | ✅ | `gui/` |
| 7.2 | Trainee web UI (login/radio/AAR) | ✅ | `frontend/src/` |
| 7.3 | Visual style (dark, tactical cues, seven-segment, large PTT) | ✅ | `gui/theme.py`, `frontend/src/styles.css` |

## 8–13. Non-functional, build, licensing

| Spec | Area | Status | Where |
|------|------|--------|-------|
| 8.3 | Reliability (flush per event, reconnect, mode preserved) | ✅ | `db/database.py` (WAL), `runtime/manager.py`, frontend reconnect |
| 8.4 | Security (LAN-only, local-only admin, checksum verify) | ✅ | `api/deps.py::require_local`, `updates/` |
| 8.6 | Logging | 🟡 | loggers in place; rotating-file config in packaging |
| 9.1–9.4 | Build, distribution, updates, uninstall | 🟡 | `packaging/pivot.spec`, `gen_buildinfo.py`, `.github/workflows/release.yml` (tag-driven Windows `.zip` + Linux x86_64 `.tar.gz` to GitHub Releases, with SHA-256 sidecars the updater verifies). Platform-aware asset selection in `updates/manager.py`. |
| 13.6 | Compliance artefacts (LICENSE/NOTICE/THIRD-PARTY/REBUILD-QT) | ✅ | repo root |
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
- Full noise-vs-frequency curve editor in the GUI (§7.1, post-v1 per §11).
