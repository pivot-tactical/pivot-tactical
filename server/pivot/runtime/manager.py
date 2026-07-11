"""The live SessionManager (control plane).

Coordinates everything that happens during an exercise: trainee login, radio
tuning and mode changes, the PTT/crypto-sync/on-air/recording lifecycle, event
logging with the correct audibility (spec §3.5.3), instructor radios and scenario
controls, and the pub/sub that pushes state to clients.

It deliberately holds no FastAPI types so it can be driven by the API, background
workers, or tests equally. Audio *media* (mic capture / Opus encode) is the audio
router's job (§6.3); this class owns the control decisions and the recording tap.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from pivot.audio.mixer import distinct_renders, group_renders, render_net_frame
from pivot.audio.pcm import float32_to_pcm16
from pivot.config import RECORDING_SAMPLE_RATE, Settings
from pivot.core.bands import (
    BandProfile,
    JammingSpan,
    format_frequency,
    parse_frequency,
    region_for,
    snap_frequency,
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
from pivot.dsp.engine import DspEngine

# Fallback power-on frequency used when no operator default is configured. The
# effective default is the ``default_frequency_hz`` config key (instructor
# Settings page); this constant only seeds that default.
DEFAULT_FREQUENCY_HZ = 7_000_000.0  # a quiet HF spot to power on at

# Auto-assigned instructor radio labels ("Radio 1", "Radio 2", …). Only labels
# matching this pattern are renumbered after a removal; custom labels are kept.
_DEFAULT_RADIO_LABEL = re.compile(r"^Radio \d+$")


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
    # Monotonic per-trainee connection counter. A reconnect bumps it so a stale
    # connection's late teardown can tell it has been superseded (§3.4.4).
    epoch: int = 0


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
            # Resume a scenario that was running when the server went down (e.g.
            # an update restart). The session lives in the DB as an un-ended row;
            # without this a restart silently ends it — trainees stay connected
            # but session_active flips to False, dropping the ambient hash and
            # breaking the "scenarios survive a restart" requirement (§3.1).
            resumed = repo.active_session(s)
            self.current_session_id: str | None = resumed.id if resumed else None
            self.current_session_name: str | None = resumed.name if resumed else None
        self.terminals: dict[str, TerminalInfo] = {}
        self._active_tx: dict[str, _TxAccumulator] = {}
        self._subscribers: set[asyncio.Queue] = set()
        # Live audio: DSP engine + per-radio outbound sinks (a callable that
        # delivers rendered PCM bytes to that radio's WebSocket, §6.3).
        self.engine = DspEngine(sample_rate=RECORDING_SAMPLE_RATE)
        self._audio_sinks: dict[str, object] = {}
        # Callbacks fired whenever the instructor radio set changes (any path:
        # WS message or REST). The instructor WS session uses one to keep a
        # tagged audio sink bound per radio (§3.2.2).
        self._instructor_radio_watchers: list[Callable[[], None]] = []
        # The server's asyncio loop, set by the app on startup. Lets background
        # threads (e.g. the transcription worker) broadcast safely into the
        # server loop via call_soon_threadsafe.
        self.loop: asyncio.AbstractEventLoop | None = None
        # Optional async transcription worker (§3.5.2). Attached by the app when
        # the transcription extra is available; events are queued on PTT release.
        self.transcription_worker = None
        # Background update service (§3.7). Attached by the app on startup; the
        # session lifecycle nudges it so deferred auto-updates apply out-of-band.
        self.update_service = None

    # -- pub/sub ----------------------------------------------------------- #

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def broadcast(self, message_type: str, payload: dict) -> None:
        """Fan a ``{type, payload}`` envelope out to all subscribers (§6.2).

        Safe to call from another thread: if the server loop is known and we are
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
            self.current_session_name = row.name
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
        self.current_session_name = None
        self._active_tx.clear()
        if result:
            self.broadcast("session_ended", result)
        # A deferred auto-update may now apply out-of-band (§3.7.1).
        if self.update_service is not None:
            self.update_service.trigger()
        return result

    # -- trainee terminals ------------------------------------------------- #

    def login(self, name: str, trainee_id: str) -> dict:
        """Register/refresh a terminal and (re)create its radio (§3.2.1).

        On reconnect within a session the radio resumes on its persisted
        frequency and crypto mode — mode is never auto-reset (§3.4.4, §8.3).
        """
        mode = RadioMode.PLAIN
        with self.db.session() as s:
            freq_hz = ConfigStore(s).default_frequency_hz()
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
        prev = self.terminals.get(trainee_id)
        epoch = (prev.epoch + 1) if prev is not None else 1
        self.terminals[trainee_id] = TerminalInfo(
            trainee_id=trainee_id,
            name=name,
            radio_id=radio_id,
            connected_at=to_iso_utc(utc_now()),
            epoch=epoch,
        )
        self._persist_radio_state(radio_id)
        self.broadcast("terminal_update", {"terminals": self.monitor_snapshot()})
        return {
            "trainee_id": trainee_id,
            "radio_id": radio_id,
            "frequency": format_frequency(freq_hz),
            "frequency_hz": freq_hz,
            "mode": mode.value,
            "epoch": epoch,
        }

    def disconnect(self, trainee_id: str, epoch: int | None = None) -> None:
        # If a newer connection for this trainee has logged in since (reconnect),
        # this is a stale teardown — leave the live terminal/radio in place.
        current = self.terminals.get(trainee_id)
        if epoch is not None and current is not None and current.epoch != epoch:
            return
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
        # Snap to the nearest valid 12.5 kHz channel — the server is authoritative,
        # so off-grid frequencies (typed or from any client) are corrected.
        freq_hz = snap_frequency(parse_frequency(frequency))
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
            radio.frequency_hz = snap_frequency(parse_frequency(frequency))
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

    # -- live audio routing (§6.3) ----------------------------------------- #

    def register_audio_sink(self, radio_id: str, sink) -> None:
        """Register a ``sink(bytes)`` that delivers rendered PCM to a radio's
        WebSocket. Called by the WS handler on connect. A newer connection for
        the same radio replaces the older one's sink."""
        self._audio_sinks[radio_id] = sink

    def unregister_audio_sink(self, radio_id: str, sink=None) -> None:
        """Drop a radio's audio sink on disconnect.

        When ``sink`` is given, only unregister if it is still the *current*
        sink for that radio. A browser reconnect (same trainee_id/instructor
        radio) registers a new sink before the stale connection's teardown runs;
        without this identity check the late teardown would clobber the fresh
        sink, silencing live audio and the ambient hash on the new session.
        """
        if sink is not None and self._audio_sinks.get(radio_id) is not sink:
            return  # a newer connection already owns this radio's sink
        self._audio_sinks.pop(radio_id, None)

    def route_tx_frame(self, radio_id: str, pcm: np.ndarray) -> None:
        """Tap a transmitter's mic frame for recording, then render it per
        listener on the same net and push the result to each listener's sink.

        The transmitter is half-duplex (never hears itself). Single-transmitter
        renders (clear voice / encrypted hash) are exact; live collision renders
        are approximate (each socket only carries its own frames), while the
        per-station recording remains clean and correct (§3.5.1).
        """
        radio = self.registry.get(radio_id)
        if radio is None or pcm.size == 0:
            return
        # Recording tap (clean, pre-DSP). Tapped from key-down — including the
        # crypto-sync lead-in, before the station is on air — so the recording
        # captures the whole transmission and is not clipped at the start
        # (§3.5.1). push_tx_audio only accumulates while a keying is active.
        self.push_tx_audio(radio_id, pcm)
        if not radio.on_air:
            return  # nothing is rendered to listeners until the station is on air

        freq = radio.frequency_hz
        render_map = self.registry.render_map_for_net(freq)
        conditions = self.band_profile.conditions_at(freq)
        # An instructor radio with its noise toggle off hears the same reception
        # (the crypto rules are unchanged) rendered over a noiseless channel, so
        # it gets its own render pass apart from the listeners hearing the real
        # conditions.
        for sub_map, cond in self.split_rx_noise(render_map, conditions):
            needed = distinct_renders(sub_map)
            if not needed:
                continue
            rendered = render_net_frame({radio_id: pcm}, cond, needed, self.engine)
            for reception, listeners in group_renders(sub_map).items():
                frame = rendered.get(reception)
                if frame is None:
                    continue
                data = float32_to_pcm16(frame)
                for listener_id in listeners:
                    sink = self._audio_sinks.get(listener_id)
                    if sink is not None:
                        self._emit_to_sink(sink, data)

    def split_rx_noise(self, render_map: dict, conditions):
        """Partition a net's render map into render passes: listeners hearing
        the channel as it is, and radios whose noise toggle is off hearing it
        noiseless. Most frames have no toggled radio and stay one pass. Shared
        with the WebRTC router's render loop."""
        quiet = {rid: rec for rid, rec in render_map.items() if self._rx_noise_off(rid)}
        if not quiet:
            return [(render_map, conditions)]
        loud = {rid: rec for rid, rec in render_map.items() if rid not in quiet}
        return [(loud, conditions), (quiet, conditions.without_noise())]

    def _rx_noise_off(self, radio_id: str) -> bool:
        radio = self.registry.get(radio_id)
        return radio is not None and not radio.rx_noise

    def render_idle_noise_tick(self, frame_samples: int, primed: set[str]) -> None:
        """Emit one ambient noise-floor frame to every idle listener (§3.2.2).

        Driven ~50×/s by the noise broadcaster on the event loop (open squelch).
        One frame is generated per active net and sent *identically* to all its
        idle listeners, so everyone on a frequency hears the same floor and the
        instructor's recording carries the matching conditions. Nets with a
        station on-air are skipped — those listeners get the live transmission
        render (which already carries the band noise) via ``route_tx_frame``.

        ``primed`` is owned by the broadcaster: the first time a radio is seen
        idle it gets a couple of extra frames to build a small jitter cushion,
        so the player worklet never underruns into silence between frames.
        """
        if not self.session_active or not self._audio_sinks:
            return
        # Group idle, sink-bound radios by net; keep a representative frequency.
        groups: dict[int, list[str]] = {}
        freq_of: dict[int, float] = {}
        for rid in list(self._audio_sinks.keys()):
            radio = self.registry.get(rid)
            if radio is None or radio.transmitting or not radio.rx_noise:
                # Not an idle listener right now (keyed, gone, or its noise
                # toggle is off — a noiseless receive has no ambient hash).
                primed.discard(rid)
                continue
            key = self.registry.net_key(radio.frequency_hz)
            groups.setdefault(key, []).append(rid)
            freq_of[key] = radio.frequency_hz

        # At most one idle frame per distinct sink per tick: an instructor binds
        # several radios to one queue, and N frames/tick would overflow it.
        sent_sinks: set[int] = set()
        for key, listeners in groups.items():
            freq = freq_of[key]
            if self.registry.active_transmitters_on_net(freq):
                for rid in listeners:
                    primed.discard(rid)
                continue
            conditions = self.band_profile.conditions_at(freq)
            data: bytes | None = None  # generated lazily, shared across the net
            for rid in listeners:
                sink = self._audio_sinks.get(rid)
                if sink is None or id(sink) in sent_sinks:
                    continue
                if data is None:
                    data = float32_to_pcm16(self.engine.render_idle_noise(frame_samples, conditions))
                if rid not in primed:
                    for _ in range(2):  # prime a ~2-frame cushion on first sight
                        self._emit_to_sink(
                            sink,
                            float32_to_pcm16(self.engine.render_idle_noise(frame_samples, conditions)),
                        )
                    primed.add(rid)
                self._emit_to_sink(sink, data)
                sent_sinks.add(id(sink))

    @staticmethod
    def _emit_to_sink(sink, data: bytes) -> None:
        try:
            sink(data)
        except Exception:  # a slow/closed sink must never break the loop
            pass

    def ptt_end(self, radio_id: str, audio: np.ndarray | None = None) -> dict | None:
        """Key up (§3.2.3). Finalise recording + event with audibility."""
        return self._finish_tx(radio_id, SyncStatus.COMPLETED, audio)

    def ptt_abort(self, radio_id: str, audio: np.ndarray | None = None) -> dict | None:
        """PTT released during crypto sync: nothing reached the air, but the
        attempt is still recorded and transcribed (§3.2.3, §3.5.1)."""
        return self._finish_tx(radio_id, SyncStatus.ABORTED, audio)

    # -- instructor radios ------------------------------------------------- #

    def add_instructor_radio(self, label: str | None = None,
                             frequency: str | float | None = None) -> dict:
        with self.db.session() as s:
            if frequency is None:
                frequency = ConfigStore(s).default_frequency_hz()
            freq_hz = snap_frequency(parse_frequency(frequency))
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
        self._instructor_radios_changed()
        return self._radio_dict(self.registry.get(radio_id))

    def remove_instructor_radio(self, radio_id: str) -> bool:
        if not radio_id.startswith("instr-"):
            return False
        db_id = int(radio_id.split("-", 1)[1])
        self.registry.remove(radio_id)
        # Detach the radio's audio sink here, not in the WS handler: a removal
        # over REST never passes through the socket's message loop, and a sink
        # left behind would keep "receiving" for a radio that no longer exists.
        self.unregister_audio_sink(radio_id)
        with self.db.session() as s:
            repo.remove_instructor_radio(s, db_id)
            self._renumber_instructor_radios(s)
        self._touch_monitor()
        self._instructor_radios_changed()
        return True

    def instructor_radios(self) -> list[dict]:
        return [self._radio_dict(r) for r in self.registry.all() if r.is_instructor]

    def set_rx_noise(self, radio_id: str, enabled: bool) -> dict:
        """Toggle the channel noise on one instructor radio's *receive* only
        (§3.1.5): off, the instructor monitors that net noiseless while every
        other station keeps hearing the channel as set. Personal to the radio —
        the net itself is shaped via the per-net scenario, not this."""
        radio = self.registry.get(radio_id)
        if radio is None or not radio.is_instructor:
            raise KeyError(f"not an instructor radio: {radio_id}")
        radio.rx_noise = enabled
        # Keep every open instructor console in step, like tune/mode changes.
        self.broadcast("instructor_radios", self.instructor_radios())
        return self._radio_dict(radio)

    def watch_instructor_radios(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register ``callback()`` to fire whenever an instructor radio is
        added or removed, regardless of which API path did it. Returns an
        unwatch function. Callbacks must be cheap and non-blocking — they may
        run on a REST worker thread."""
        self._instructor_radio_watchers.append(callback)

        def unwatch() -> None:
            try:
                self._instructor_radio_watchers.remove(callback)
            except ValueError:
                pass

        return unwatch

    def _instructor_radios_changed(self) -> None:
        for cb in list(self._instructor_radio_watchers):
            try:
                cb()
            except Exception:  # a broken watcher must not block the change
                pass
        # Keep every open instructor console in step (labels renumber on
        # removal, and another tab/REST client may have made the change).
        self.broadcast("instructor_radios", self.instructor_radios())

    def _renumber_instructor_radios(self, s) -> None:
        """Re-fit default "Radio N" labels to each radio's list position so the
        card numbering in the instructor console (1, 2, 3, …) and the radio's
        displayed name agree after a removal. Custom labels are untouched."""
        for idx, row in enumerate(repo.list_instructor_radios(s), start=1):
            if not _DEFAULT_RADIO_LABEL.match(row.label):
                continue
            new_label = f"Radio {idx}"
            if row.label == new_label:
                continue
            row.label = new_label
            radio = self.registry.get(f"instr-{row.id}")
            if radio is not None:
                radio.label = new_label

    # -- scenario controls (§3.1.5) ---------------------------------------- #

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

    def set_net_scenario(
        self,
        frequency: str | float,
        *,
        interference: float | None = None,
        jammed: bool | None = None,
    ) -> dict:
        """Per-net instructor override: interference level / jammer on one
        channel (§3.1.5). Only the passed fields change; the full override list
        is persisted and broadcast so every console stays in step."""
        freq_hz = snap_frequency(parse_frequency(frequency))
        scenario = self.band_profile.set_net_scenario(
            freq_hz, interference=interference, jammed=jammed
        )
        self._save_band_profile()
        self.broadcast(
            "band_profile_update",
            {"net_scenarios": self.band_profile.net_scenarios_to_json()},
        )
        return scenario.to_dict()

    def set_crypto_enabled(self, enabled: bool) -> None:
        self.band_profile.crypto_enabled = enabled
        self._save_band_profile()
        self.broadcast("band_profile_update", {"crypto_enabled": enabled})

    def update_curve(self, anchors_json: list[dict]) -> None:
        self.band_profile = BandProfile.from_curve_json(
            anchors_json,
            jamming=self.band_profile.jamming,
            net_scenarios=self.band_profile.net_scenarios,
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
            "crypto_enabled": self.band_profile.crypto_enabled,
            "crypto_delay_ms": self.band_profile.crypto_delay_ms,
            "jamming": [[j.low_hz, j.high_hz] for j in self.band_profile.jamming],
            "net_scenarios": self.band_profile.net_scenarios_to_json(),
        }

    # -- config ------------------------------------------------------------ #

    def get_config(self) -> dict:
        with self.db.session() as s:
            return ConfigStore(s).all()

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

    def edit_transcription(self, event_id: str, new_text: str) -> dict | None:
        """Apply an instructor's manual transcript correction and fan it out.

        Persists the edit (preserving the machine transcription for diffing) and
        broadcasts the same ``transcription_updated`` message the worker uses, so
        every open console reflects the correction live (§3.5.3). Returns the
        updated event dict, or ``None`` when the event does not exist.
        """
        with self.db.session() as s:
            row = repo.edit_transcription(s, event_id, new_text)
            payload = row.to_dict() if row is not None else None
        if payload is not None:
            self.broadcast("transcription_updated", payload)
        return payload

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
        from pivot.db.models import TranscriptionStatus

        conditions = self.band_profile.conditions_at(acc.frequency_hz)
        rel_path = relative_audio_path(self.current_session_id, acc.event_id)
        dur = 0
        has_audio = clean is not None and clean.size > 0
        if has_audio:
            path = event_audio_path(
                self.settings.recordings_dir, self.current_session_id, acc.event_id
            )
            write_recording(path, clean, RECORDING_SAMPLE_RATE)
            dur = duration_ms(clean, RECORDING_SAMPLE_RATE)

        # No audio captured → terminal "Skipped" so the UI doesn't sit on
        # "transcribing…"; otherwise Pending for the async worker.
        status = TranscriptionStatus.PENDING if has_audio else TranscriptionStatus.SKIPPED

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
                transcription_status=status,
            )
            result = row.to_dict()

        # Queue async transcription (never blocks live audio, §3.5.2). Only when
        # there is audio to transcribe.
        if self.transcription_worker is not None and has_audio:
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
            "rx_noise": radio.rx_noise,
        }
