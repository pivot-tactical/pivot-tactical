"""The live SessionManager (control plane).

Coordinates everything that happens during an exercise: trainee login, radio
tuning and mode changes, the PTT/crypto-sync/on-air/recording lifecycle, event
logging with the correct audibility (spec §3.5.3), instructor radios and scenario
controls, and the pub/sub that pushes state to clients.

It deliberately holds no FastAPI or Qt types so it can be driven by the API, the
GUI, or tests equally. Audio *media* (mic capture / Opus encode) is the audio
router's job (§6.3); this class owns the control decisions and the recording tap.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import numpy as np

from pivot.config import RECORDING_SAMPLE_RATE, Settings
from pivot.core.bands import (
    BandProfile,
    JammingSpan,
    format_frequency,
    parse_frequency,
    region_for,
)
from pivot.core.crypto import (
    Audibility,
    RadioMode,
    SyncStatus,
    TxOutcome,
    classify_audibility,
)
from pivot.core.radios import INSTRUCTOR_OWNER, Radio, RadioRegistry
from pivot.core.timebase import to_iso_utc, utc_now
from pivot.db import repository as repo
from pivot.db.config_store import ConfigStore
from pivot.db.database import Database

DEFAULT_FREQUENCY_HZ = 145_500_000.0  # a quiet VHF spot to power on at


def _on_loop(loop: asyncio.AbstractEventLoop) -> bool:
    """True if the calling thread is running ``loop`` right now."""
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


@dataclass
class TerminalInfo:
    """A connected trainee terminal (spec §3.1.4 live monitor)."""

    trainee_id: str
    name: str
    radio_id: str
    connected_at: str


@dataclass
class _TxAccumulator:
    """Per-keying bookkeeping used to classify audibility at PTT release."""

    event_id: str
    radio_id: str
    trainee_name: str
    frequency_hz: float
    tx_mode: RadioMode
    started_at: str
    had_listener: bool = False
    overlapped_modes: list[RadioMode] = field(default_factory=list)
    keyed_first: bool = True
    audio_chunks: list[np.ndarray] = field(default_factory=list)


class SessionManager:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        with db.session() as s:
            cfg = ConfigStore(s)
            self.registry = RadioRegistry(tuning_step_hz=cfg.tuning_step_hz())
            self.band_profile = repo.load_band_profile(s)
            self._load_instructor_radios(s)
        self.current_session_id: str | None = None
        self.terminals: dict[str, TerminalInfo] = {}
        self._active_tx: dict[str, _TxAccumulator] = {}
        self._subscribers: set[asyncio.Queue] = set()
        # The server's asyncio loop, set by the app on startup. Lets the GUI
        # thread broadcast safely into the server loop (the GUI and server share
        # one manager, spec §2.3).
        self.loop: asyncio.AbstractEventLoop | None = None
        # Optional async transcription worker (§3.5.2). Attached by the app when
        # the transcription extra is available; events are queued on PTT release.
        self.transcription_worker = None

    # -- pub/sub ----------------------------------------------------------- #

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def broadcast(self, message_type: str, payload: dict) -> None:
        """Fan a ``{type, payload}`` envelope out to all subscribers (§6.2).

        Safe to call from the GUI thread: if the server loop is known and we are
        not running on it, the fan-out is marshalled onto that loop.
        """
        msg = {"type": message_type, "payload": payload}
        loop = self.loop
        if loop is not None and not _on_loop(loop):
            loop.call_soon_threadsafe(self._fanout, msg)
        else:
            self._fanout(msg)

    def _fanout(self, msg: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:  # pragma: no cover - defensive
                pass

    # -- session lifecycle ------------------------------------------------- #

    @property
    def session_active(self) -> bool:
        return self.current_session_id is not None

    def start_session(self, name: str) -> dict:
        with self.db.session() as s:
            row = repo.start_session(s, name)
            self.current_session_id = row.id
            result = {"id": row.id, "name": row.name, "started_at": row.started_at}
        self.broadcast("session_started", result)
        return result

    def end_session(self) -> dict | None:
        if not self.current_session_id:
            return None
        with self.db.session() as s:
            row = repo.end_session(s, self.current_session_id)
            result = (
                {"id": row.id, "name": row.name, "ended_at": row.ended_at} if row else None
            )
        self.current_session_id = None
        self._active_tx.clear()
        if result:
            self.broadcast("session_ended", result)
        return result

    # -- trainee terminals ------------------------------------------------- #

    def login(self, name: str, trainee_id: str) -> dict:
        """Register/refresh a terminal and (re)create its radio (§3.2.1).

        On reconnect within a session the radio resumes on its persisted
        frequency and crypto mode — mode is never auto-reset (§3.4.4, §8.3).
        """
        freq_hz = DEFAULT_FREQUENCY_HZ
        mode = RadioMode.PLAIN
        with self.db.session() as s:
            repo.upsert_trainee(s, trainee_id, name)
            if self.current_session_id:
                state = repo.get_radio_state(s, self.current_session_id, trainee_id)
                if state is not None:
                    freq_hz = parse_frequency(state.frequency)
                    mode = state.mode if isinstance(state.mode, RadioMode) else RadioMode(state.mode)

        radio_id = trainee_id
        existing = self.registry.get(radio_id)
        if existing is not None:
            existing.frequency_hz = freq_hz
            existing.mode = mode
        else:
            self.registry.add(
                Radio(
                    radio_id=radio_id,
                    owner=trainee_id,
                    label=name,
                    frequency_hz=freq_hz,
                    mode=mode,
                )
            )
        self.terminals[trainee_id] = TerminalInfo(
            trainee_id=trainee_id,
            name=name,
            radio_id=radio_id,
            connected_at=to_iso_utc(utc_now()),
        )
        self._persist_radio_state(radio_id)
        self.broadcast("terminal_update", {"terminals": self.monitor_snapshot()})
        return {
            "trainee_id": trainee_id,
            "radio_id": radio_id,
            "frequency": format_frequency(freq_hz),
            "frequency_hz": freq_hz,
            "mode": mode.value,
        }

    def disconnect(self, trainee_id: str) -> None:
        self.terminals.pop(trainee_id, None)
        # Keep radio_state persisted (mode survives); drop the live radio so it
        # leaves the frequency map.
        if trainee_id in self._active_tx:
            self.ptt_abort(trainee_id)
        self.registry.remove(trainee_id)
        self.broadcast("terminal_update", {"terminals": self.monitor_snapshot()})

    def kick(self, trainee_id: str) -> bool:
        """Instructor kicks a terminal from the session (§3.1.5)."""
        if trainee_id not in self.terminals and self.registry.get(trainee_id) is None:
            return False
        self.disconnect(trainee_id)
        self.broadcast("kicked", {"trainee_id": trainee_id})
        return True

    # -- tuning & mode ----------------------------------------------------- #

    def tune(self, radio_id: str, frequency: str | float) -> dict:
        freq_hz = parse_frequency(frequency)
        radio = self.registry.tune(radio_id, freq_hz)
        self._persist_radio_state(radio_id)
        self._touch_monitor()
        return self._radio_dict(radio)

    def set_mode(self, radio_id: str, mode: RadioMode) -> dict:
        radio = self.registry.set_mode(radio_id, mode)
        self._persist_radio_state(radio_id)
        self._touch_monitor()
        return self._radio_dict(radio)

    # -- PTT lifecycle ----------------------------------------------------- #

    def ptt_start(self, radio_id: str, frequency: str | float | None = None,
                  tx_mode: RadioMode | None = None) -> dict:
        """Key down (§3.2.3). Returns ``{event_id, sync_applies, sync_delay_ms}``."""
        radio = self.registry.get(radio_id)
        if radio is None:
            raise KeyError(f"unknown radio: {radio_id}")
        # Honour the frequency/mode reported at key-down (the client is the truth
        # for the instant of keying), else use the radio's current state.
        if frequency is not None:
            radio.frequency_hz = parse_frequency(frequency)
        if tx_mode is not None:
            radio.mode = tx_mode

        crypto_enabled = self.band_profile.crypto_enabled
        event_id = repo.new_uuid()
        acc = _TxAccumulator(
            event_id=event_id,
            radio_id=radio_id,
            trainee_name=self._display_name(radio),
            frequency_hz=radio.frequency_hz,
            tx_mode=radio.mode,
            started_at=to_iso_utc(utc_now()),
        )
        self._active_tx[radio_id] = acc

        sync_applies = self.registry.begin_key(radio_id, event_id, crypto_enabled=crypto_enabled)
        if not sync_applies:
            self._go_on_air(radio_id)  # plain (or crypto-disabled) → immediate air
        self._touch_monitor()
        return {
            "event_id": event_id,
            "sync_applies": sync_applies,
            "sync_delay_ms": self.band_profile.crypto_delay_ms if sync_applies else 0,
        }

    def ptt_sync_complete(self, radio_id: str) -> None:
        """Crypto sync delay elapsed: station goes on-air (§3.2.3)."""
        self.registry.sync_complete(radio_id)
        self._go_on_air(radio_id)
        self._touch_monitor()

    def push_tx_audio(self, radio_id: str, audio: np.ndarray) -> None:
        """Append a chunk of the station's clean mic audio for recording."""
        acc = self._active_tx.get(radio_id)
        if acc is not None:
            acc.audio_chunks.append(np.asarray(audio, dtype=np.float32).reshape(-1))

    def ptt_end(self, radio_id: str, audio: np.ndarray | None = None) -> dict | None:
        """Key up (§3.2.3). Finalise recording + event with audibility."""
        return self._finish_tx(radio_id, SyncStatus.COMPLETED, audio)

    def ptt_abort(self, radio_id: str, audio: np.ndarray | None = None) -> dict | None:
        """PTT released during crypto sync: nothing reached the air, but the
        attempt is still recorded and transcribed (§3.2.3, §3.5.1)."""
        return self._finish_tx(radio_id, SyncStatus.ABORTED, audio)

    # -- instructor radios ------------------------------------------------- #

    def add_instructor_radio(self, label: str | None = None,
                             frequency: str | float = DEFAULT_FREQUENCY_HZ) -> dict:
        freq_hz = parse_frequency(frequency)
        with self.db.session() as s:
            existing = repo.list_instructor_radios(s)
            label = label or f"Radio {len(existing) + 1}"
            row = repo.add_instructor_radio(s, label, format_frequency(freq_hz))
            radio_id = f"instr-{row.id}"
        self.registry.add(
            Radio(
                radio_id=radio_id,
                owner=INSTRUCTOR_OWNER,
                label=label,
                frequency_hz=freq_hz,
                is_instructor=True,
            )
        )
        self._touch_monitor()
        return self._radio_dict(self.registry.get(radio_id))

    def remove_instructor_radio(self, radio_id: str) -> bool:
        if not radio_id.startswith("instr-"):
            return False
        db_id = int(radio_id.split("-", 1)[1])
        with self.db.session() as s:
            repo.remove_instructor_radio(s, db_id)
        self.registry.remove(radio_id)
        self._touch_monitor()
        return True

    def instructor_radios(self) -> list[dict]:
        return [self._radio_dict(r) for r in self.registry.all() if r.is_instructor]

    # -- scenario controls (§3.1.5) ---------------------------------------- #

    def set_atmospheric(self, multiplier: float) -> None:
        self.band_profile.atmospheric_multiplier = max(0.0, multiplier)
        self._save_band_profile()
        self.broadcast("band_profile_update", {"atmospheric_multiplier": multiplier})

    def set_jamming(self, spans: list[tuple[float, float]] | None) -> None:
        self.band_profile.jamming = [JammingSpan(lo, hi) for lo, hi in (spans or [])]
        self.broadcast(
            "scenario_event",
            {"jamming": [[j.low_hz, j.high_hz] for j in self.band_profile.jamming]},
        )

    def toggle_jamming(self, low_hz: float, high_hz: float, on: bool) -> None:
        spans = [(j.low_hz, j.high_hz) for j in self.band_profile.jamming]
        span = (low_hz, high_hz)
        if on and span not in spans:
            spans.append(span)
        elif not on and span in spans:
            spans.remove(span)
        self.set_jamming(spans)

    def inject_noise_burst(self, low_hz: float, high_hz: float) -> None:
        """Instant noise burst on a frequency/span (§3.1.5)."""
        self.broadcast("scenario_event", {"noise_burst": [low_hz, high_hz]})

    def set_crypto_enabled(self, enabled: bool) -> None:
        self.band_profile.crypto_enabled = enabled
        self._save_band_profile()
        self.broadcast("band_profile_update", {"crypto_enabled": enabled})

    def update_curve(self, anchors_json: list[dict]) -> None:
        self.band_profile = BandProfile.from_curve_json(
            anchors_json,
            atmospheric_multiplier=self.band_profile.atmospheric_multiplier,
            crypto_delay_ms=self.band_profile.crypto_delay_ms,
            crypto_enabled=self.band_profile.crypto_enabled,
        )
        self._save_band_profile()
        self.broadcast("band_profile_update", {"curve": self.band_profile.curve_to_json()})

    def set_display_timezone(self, tz_name: str) -> None:
        with self.db.session() as s:
            ConfigStore(s).set("display_timezone", tz_name)
        self.broadcast("timezone_update", {"timezone": tz_name})

    # -- instructor monitor (§3.1.4) --------------------------------------- #

    def monitor_snapshot(self) -> list[dict]:
        out = []
        for r in self.registry.all():
            out.append(
                {
                    "radio_id": r.radio_id,
                    "name": self._display_name(r),
                    "is_instructor": r.is_instructor,
                    "frequency": format_frequency(r.frequency_hz),
                    "frequency_hz": r.frequency_hz,
                    "band_region": region_for(r.frequency_hz).label,
                    "mode": r.mode.value,
                    "status": r.status,
                    "last_activity": to_iso_utc(r.last_activity),
                }
            )
        return out

    def band_profile_snapshot(self) -> dict:
        return {
            "curve": self.band_profile.curve_to_json(),
            "atmospheric_multiplier": self.band_profile.atmospheric_multiplier,
            "crypto_enabled": self.band_profile.crypto_enabled,
            "crypto_delay_ms": self.band_profile.crypto_delay_ms,
            "jamming": [[j.low_hz, j.high_hz] for j in self.band_profile.jamming],
        }

    # -- internals --------------------------------------------------------- #

    def _go_on_air(self, radio_id: str) -> None:
        """Record overlap/ordering/listener facts at the moment a station keys
        the air, updating every concurrently-active accumulator."""
        acc = self._active_tx.get(radio_id)
        radio = self.registry.get(radio_id)
        if acc is None or radio is None:
            return
        others = self.registry.active_transmitters_on_net(radio.frequency_hz, exclude=radio_id)
        on_air_others = [o for o in others if o.on_air and o.radio_id != radio_id]
        if on_air_others:
            acc.keyed_first = False
            for o in on_air_others:
                acc.overlapped_modes.append(o.mode)
                other_acc = self._active_tx.get(o.radio_id)
                if other_acc is not None:
                    other_acc.overlapped_modes.append(acc.tx_mode)
        if self.registry.has_listener(radio.frequency_hz, exclude=radio_id):
            acc.had_listener = True

    def _finish_tx(self, radio_id: str, sync_status: SyncStatus,
                   audio: np.ndarray | None) -> dict | None:
        acc = self._active_tx.pop(radio_id, None)
        radio = self.registry.get(radio_id)
        self.registry.end_key(radio_id) if radio is not None else None
        if acc is None:
            return None

        # Re-check for a listener that may have tuned in mid-transmission.
        if not acc.had_listener and self.registry.has_listener(
            acc.frequency_hz, exclude=radio_id
        ):
            acc.had_listener = True

        if sync_status is SyncStatus.ABORTED:
            audibility = Audibility.UNHEARD_NO_LISTENERS  # never reached the air
        else:
            audibility = classify_audibility(
                TxOutcome(
                    self_mode=acc.tx_mode,
                    had_listener=acc.had_listener,
                    overlapped_modes=acc.overlapped_modes,
                    keyed_first=acc.keyed_first,
                )
            )

        clean = self._collect_audio(acc, audio)
        event = self._write_event(acc, sync_status, audibility, clean)
        self._touch_monitor()
        if event is not None:
            # Push the full event so the instructor's live log renders it
            # immediately; the transcript fills in later via notify_transcription.
            self.broadcast("event_logged", event)
        return event

    def notify_transcription(self, event_id: str) -> None:
        """Broadcast a transcription update for ``event_id`` (called by the
        transcription worker on completion, §3.5.2)."""
        with self.db.session() as s:
            row = repo.get_event(s, event_id)
            if row is not None:
                self.broadcast("transcription_updated", row.to_dict())

    def _collect_audio(self, acc: _TxAccumulator, audio: np.ndarray | None) -> np.ndarray | None:
        if audio is not None:
            return np.asarray(audio, dtype=np.float32).reshape(-1)
        if acc.audio_chunks:
            return np.concatenate(acc.audio_chunks).astype(np.float32)
        return None

    def _write_event(self, acc, sync_status, audibility, clean) -> dict | None:
        if self.current_session_id is None:
            return None  # events only logged within a session
        from pivot.audio.recording import (
            duration_ms,
            event_audio_path,
            relative_audio_path,
            write_recording,
        )

        conditions = self.band_profile.conditions_at(acc.frequency_hz)
        rel_path = relative_audio_path(self.current_session_id, acc.event_id)
        dur = 0
        if clean is not None and clean.size:
            path = event_audio_path(
                self.settings.recordings_dir, self.current_session_id, acc.event_id
            )
            write_recording(path, clean, RECORDING_SAMPLE_RATE)
            dur = duration_ms(clean, RECORDING_SAMPLE_RATE)

        with self.db.session() as s:
            row = repo.create_event(
                s,
                event_id=acc.event_id,
                session_id=self.current_session_id,
                trainee_name=acc.trainee_name,
                frequency=format_frequency(acc.frequency_hz),
                band_region=region_for(acc.frequency_hz).label,
                tx_mode=acc.tx_mode,
                audibility=audibility,
                sync_status=sync_status,
                timestamp_start=acc.started_at,
                duration_ms=dur,
                audio_path=rel_path,
                dsp_profile=conditions.to_dict(),
            )
            result = row.to_dict()

        # Queue async transcription (never blocks live audio, §3.5.2). Only when
        # there is audio to transcribe.
        if self.transcription_worker is not None and clean is not None and clean.size:
            self.transcription_worker.enqueue(acc.event_id)
        return result

    def _load_instructor_radios(self, s) -> None:
        for row in repo.list_instructor_radios(s):
            radio_id = f"instr-{row.id}"
            self.registry.add(
                Radio(
                    radio_id=radio_id,
                    owner=INSTRUCTOR_OWNER,
                    label=row.label,
                    frequency_hz=parse_frequency(row.frequency),
                    is_instructor=True,
                    mode=row.mode if isinstance(row.mode, RadioMode) else RadioMode(row.mode),
                )
            )

    def _persist_radio_state(self, radio_id: str) -> None:
        radio = self.registry.get(radio_id)
        if radio is None or radio.is_instructor or self.current_session_id is None:
            return
        with self.db.session() as s:
            repo.upsert_radio_state(
                s,
                self.current_session_id,
                radio.owner,
                format_frequency(radio.frequency_hz),
                radio.mode,
            )

    def _save_band_profile(self) -> None:
        with self.db.session() as s:
            repo.save_band_profile(s, self.band_profile)

    def _touch_monitor(self) -> None:
        self.broadcast("terminal_update", {"terminals": self.monitor_snapshot()})

    def _display_name(self, radio: Radio) -> str:
        if radio.is_instructor:
            return f"INSTRUCTOR ({radio.label})"
        return radio.label

    def _radio_dict(self, radio: Radio) -> dict:
        return {
            "radio_id": radio.radio_id,
            "name": self._display_name(radio),
            "is_instructor": radio.is_instructor,
            "frequency": format_frequency(radio.frequency_hz),
            "frequency_hz": radio.frequency_hz,
            "band_region": region_for(radio.frequency_hz).label,
            "mode": radio.mode.value,
            "status": radio.status,
        }
