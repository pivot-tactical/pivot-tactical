"""Radio state and the emergent-net frequency map (spec §3.1.2, §6.3).

PIVOT has no configured nets. A net is *emergent*: any radios tuned to the same
frequency are on the same net and can hear each other (§3.1.2). The server holds
a frequency map of which radios are tuned where and each radio's Plain/Cypher
mode; this module is that map.

Frequencies are quantised to a tuning grid (default 100 Hz) so that two
operators who both dial "14.250" land on the same channel and hear each other —
modelling real radio selectivity without fragile float-equality. The grid step
is configurable.

This is live, in-memory state (mirrors the transient ``radio_state`` table,
§5.1). The audio router and session manager drive transmit timing; this registry
answers "who is on this net, who is keyed, and what does each listener hear".
"""

from dataclasses import dataclass, field
from datetime import datetime

from pivot.core.bands import region_for
from pivot.core.crypto import RadioMode, Reception, resolve_listener
from pivot.core.timebase import utc_now

INSTRUCTOR_OWNER = "INSTRUCTOR"


@dataclass
class Radio:
    """A single radio — a trainee terminal's radio or one instructor radio.

    Every radio in v1.0 is a single super-radio able to tune the entire range
    (§3.1.2). ``mode`` persists across retuning and is never auto-reset (§3.4.4).
    """

    radio_id: str
    owner: str               # trainee_id, or INSTRUCTOR_OWNER for instructor radios
    label: str               # callsign / display name / "Radio 1"
    frequency_hz: float
    is_instructor: bool = False
    mode: RadioMode = RadioMode.PLAIN
    # Per-radio receive-noise toggle (instructor radios only, §3.1.5): when
    # False this radio's *received* audio is rendered over a noiseless channel
    # so the instructor can monitor unhindered. The net itself — and what every
    # other station on it hears — is untouched (that is the per-net scenario's
    # job).
    rx_noise: bool = True
    # Transmit lifecycle: a cypher keying first enters crypto sync (not on air),
    # then goes on-air when the sync delay elapses (§3.2.3, §3.4.2).
    syncing: bool = False
    on_air: bool = False
    active_event_id: str | None = None
    tx_started_at: datetime | None = None  # when this radio went on-air (for ordering)
    last_activity: datetime = field(default_factory=utc_now)

    @property
    def transmitting(self) -> bool:
        """Either syncing or on-air — i.e. PTT is held."""
        return self.syncing or self.on_air

    @property
    def status(self) -> str:
        """Human status for the live terminal monitor (§3.1.4)."""
        if self.on_air:
            return "transmitting"
        if self.syncing:
            return "crypto-sync"
        return "idle"

    @property
    def region_label(self) -> str:
        return region_for(self.frequency_hz).label


class RadioRegistry:
    """The live frequency map across all radios in a session."""

    def __init__(self, tuning_step_hz: float = 100.0) -> None:
        self.tuning_step_hz = tuning_step_hz
        self._radios: dict[str, Radio] = {}

    # -- channelisation ---------------------------------------------------- #

    def net_key(self, freq_hz: float) -> int:
        """Quantise a frequency to its net/channel index."""
        return round(freq_hz / self.tuning_step_hz)

    def same_net(self, a_hz: float, b_hz: float) -> bool:
        return self.net_key(a_hz) == self.net_key(b_hz)

    # -- registration ------------------------------------------------------ #

    def add(self, radio: Radio) -> Radio:
        self._radios[radio.radio_id] = radio
        return radio

    def remove(self, radio_id: str) -> Radio | None:
        return self._radios.pop(radio_id, None)

    def get(self, radio_id: str) -> Radio | None:
        return self._radios.get(radio_id)

    def all(self) -> list[Radio]:
        return list(self._radios.values())

    def for_owner(self, owner: str) -> list[Radio]:
        return [r for r in self._radios.values() if r.owner == owner]

    # -- mutations --------------------------------------------------------- #

    def tune(self, radio_id: str, freq_hz: float) -> Radio:
        r = self._require(radio_id)
        if r.on_air:
            # Trainee may retune at any time except during their own TX (§3.2.2).
            raise RadioBusyError("cannot retune while transmitting")
        r.frequency_hz = freq_hz
        r.last_activity = utc_now()
        return r

    def set_mode(self, radio_id: str, mode: RadioMode) -> Radio:
        r = self._require(radio_id)
        if r.transmitting:
            # Mode toggle disabled during the operator's own TX (§3.4.5).
            raise RadioBusyError("cannot change mode while transmitting")
        r.mode = mode
        r.last_activity = utc_now()
        return r

    def begin_key(
        self, radio_id: str, event_id: str, crypto_enabled: bool = True
    ) -> bool:
        """Press PTT. Returns True if a crypto sync delay applies before air.

        A Plain keying (or any keying when crypto is globally disabled) goes
        straight on-air. A Cypher keying enters the sync state first (§3.2.3).
        """
        r = self._require(radio_id)
        r.active_event_id = event_id
        r.last_activity = utc_now()
        if r.mode is RadioMode.CYPHER and crypto_enabled:
            r.syncing = True
            r.on_air = False
            return True
        r.syncing = False
        r.on_air = True
        r.tx_started_at = utc_now()
        return False

    def sync_complete(self, radio_id: str) -> Radio:
        """Crypto sync delay elapsed: mic opens, station goes on-air (§3.2.3)."""
        r = self._require(radio_id)
        if r.syncing:
            r.syncing = False
            r.on_air = True
            r.tx_started_at = utc_now()
        return r

    def end_key(self, radio_id: str) -> str | None:
        """Release PTT. Returns the event_id that was active (for logging)."""
        r = self._require(radio_id)
        event_id = r.active_event_id
        r.syncing = False
        r.on_air = False
        r.active_event_id = None
        r.tx_started_at = None
        r.last_activity = utc_now()
        return event_id

    # -- net queries ------------------------------------------------------- #

    def radios_on_net(self, freq_hz: float, exclude: str | None = None) -> list[Radio]:
        key = self.net_key(freq_hz)
        return [
            r
            for r in self._radios.values()
            if r.radio_id != exclude and self.net_key(r.frequency_hz) == key
        ]

    def active_transmitters_on_net(
        self, freq_hz: float, exclude: str | None = None
    ) -> list[Radio]:
        """Stations *on-air* (past crypto sync) on this net."""
        return [r for r in self.radios_on_net(freq_hz, exclude=exclude) if r.on_air]

    def listeners_on_net(self, freq_hz: float) -> list[Radio]:
        """Radios on this net not currently keyed (potential receivers)."""
        return [r for r in self.radios_on_net(freq_hz) if not r.transmitting]

    def has_listener(self, freq_hz: float, exclude: str | None = None) -> bool:
        return any(
            not r.transmitting for r in self.radios_on_net(freq_hz, exclude=exclude)
        )

    # -- render decisions -------------------------------------------------- #

    def render_for(self, radio_id: str) -> Reception:
        """What ``radio_id`` hears right now, per the crypto/collision rules."""
        r = self._require(radio_id)
        others = self.active_transmitters_on_net(r.frequency_hz, exclude=radio_id)
        return resolve_listener(
            rx_mode=r.mode,
            active_tx_modes=[t.mode for t in others],
            listener_is_transmitting=r.transmitting,
        )

    def render_map_for_net(self, freq_hz: float) -> dict[str, Reception]:
        """Map of radio_id -> Reception for every radio on the net.

        The audio router groups identical Receptions into one encoded stream
        (the fan-out optimisation, §6.3 / Appendix A.4).
        """
        result: dict[str, Reception] = {}
        for r in self.radios_on_net(freq_hz):
            result[r.radio_id] = self.render_for(r.radio_id)
        return result

    # -- internals --------------------------------------------------------- #

    def _require(self, radio_id: str) -> Radio:
        r = self._radios.get(radio_id)
        if r is None:
            raise KeyError(f"unknown radio: {radio_id}")
        return r


class RadioBusyError(RuntimeError):
    """Raised when an operation is illegal during transmit (§3.2.2, §3.4.5)."""
