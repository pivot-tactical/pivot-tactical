"""Frequency model: region classification and the continuous band profile.

PIVOT does not use a per-net "conditions" enum. Instead a single DSP chain has
parameters that vary *continuously* with the radio's tuned frequency (spec
§3.1.2, §4.1). As a station tunes from low HF upward, the noise floor and fading
depth decrease smoothly. The character of the audio at any frequency is a
function of:

* the editable noise-vs-frequency **curve** (anchor points, interpolated),
* a global **atmospheric multiplier** that scales the whole curve worse/better,
* any instructor-injected **jamming** on a frequency or span.

Frequencies are handled internally in **hertz** (float). Parsing/formatting
helpers convert to and from the human-readable strings stored on events and
radios (the DB stores frequency as TEXT, spec §5.1).
"""

from __future__ import annotations

import bisect
import math
import re
from dataclasses import dataclass, field, replace
from enum import Enum

# Overall tunable range: low HF through UHF (spec §3.1.2 table).
MIN_FREQ_HZ: float = 1_600_000.0          # 1.6 MHz
MAX_FREQ_HZ: float = 3_000_000_000.0      # 3 GHz


class BandRegion(str, Enum):
    """Coarse region label, used for display and the ``band_region`` event
    field (spec §3.5.3). The audio itself follows the continuous curve, not
    these buckets."""

    LOW_HF = "Low HF"
    HIGH_HF = "High HF"
    VHF = "VHF"
    UHF = "UHF"

    @property
    def label(self) -> str:
        return self.value


def region_for(freq_hz: float) -> BandRegion:
    """Classify a frequency into its display region (spec §3.1.2 boundaries).

    Boundaries: <10 MHz Low HF, 10–30 MHz High HF, 30–300 MHz VHF, >=300 MHz
    UHF. Frequencies below/above the tunable range clamp to the nearest region.
    """
    if freq_hz < 10_000_000.0:
        return BandRegion.LOW_HF
    if freq_hz < 30_000_000.0:
        return BandRegion.HIGH_HF
    if freq_hz < 300_000_000.0:
        return BandRegion.VHF
    return BandRegion.UHF


# --------------------------------------------------------------------------- #
# Frequency parsing / formatting
# --------------------------------------------------------------------------- #

_UNIT_HZ = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}
_FREQ_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([kKmMgG]?[hH]?[zZ]?)?\s*$")


def parse_frequency(text: str | float | int) -> float:
    """Parse a human frequency into hertz.

    Accepts numbers (assumed MHz if small, Hz if large) and strings with units
    such as ``"14.250 MHz"``, ``"145500 kHz"``, ``"243 MHz"`` or a bare
    ``"7.1"`` (interpreted as MHz, the operator's natural unit on these bands).
    """
    if isinstance(text, (int, float)):
        value = float(text)
        # Bare number: assume MHz unless it is already clearly in hertz.
        return value if value >= MIN_FREQ_HZ else value * 1e6

    m = _FREQ_RE.match(text)
    if not m:
        raise ValueError(f"unparseable frequency: {text!r}")
    value = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if unit in ("", "h"):  # no unit -> MHz by convention
        return value * 1e6
    # Normalise partial units like "m" / "k" / "g" to *Hz keys.
    if not unit.endswith("hz"):
        unit = unit[0] + "hz"
    if unit not in _UNIT_HZ:
        raise ValueError(f"unknown frequency unit in {text!r}")
    return value * _UNIT_HZ[unit]


def format_frequency(freq_hz: float) -> str:
    """Format hertz as a tidy operator-facing string (e.g. ``"14.250 MHz"``)."""
    if freq_hz >= 1e9:
        return f"{freq_hz / 1e9:.4f} GHz".rstrip("0").rstrip(".") + (
            "" if "." in f"{freq_hz / 1e9:.4f}" else ""
        )
    if freq_hz >= 1e6:
        return f"{freq_hz / 1e6:.3f} MHz"
    if freq_hz >= 1e3:
        return f"{freq_hz / 1e3:.3f} kHz"
    return f"{freq_hz:.0f} Hz"


def clamp_frequency(freq_hz: float) -> float:
    """Clamp to the tunable range."""
    return max(MIN_FREQ_HZ, min(MAX_FREQ_HZ, freq_hz))


# --------------------------------------------------------------------------- #
# Conditions curve
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BandConditions:
    """Fully-resolved audio parameters at one frequency and one instant.

    Produced by :meth:`BandProfile.conditions_at`. Consumed by the DSP engine
    (spec §4) to render a transmission for a listener. ``snr_db`` is the headline
    signal-to-noise figure; the remaining fields shape the noise, fading and
    squelch behaviour described in §4.1.1.
    """

    freq_hz: float
    region: BandRegion
    snr_db: float
    # Noise colour weighting: 0.0 = pure Gaussian/white, 1.0 = pink-dominant.
    pink_weight: float
    # Fading model (Rayleigh): peak-to-trough depth and how fast it varies.
    fading_depth_db: float
    fading_rate_hz: float
    selective_fading: bool          # frequency-selective notch (HF only, §4.1.1)
    qrm: bool                       # low-level interfering carriers (HF only)
    # Voice bandpass, narrowing slightly at the noisy low-HF end (§4.1.1).
    bandpass_low_hz: float
    bandpass_high_hz: float
    squelch_tail_ms: float          # tail length, longer on HF
    jammed: bool = False            # instructor jamming covers this frequency

    @property
    def snr_linear(self) -> float:
        return 10.0 ** (self.snr_db / 20.0)

    def to_dict(self) -> dict:
        """Serialise for the event's ``dsp_profile_json`` (spec §3.5.3, §4.5)."""
        return {
            "freq_hz": self.freq_hz,
            "region": self.region.value,
            "snr_db": self.snr_db,
            "pink_weight": self.pink_weight,
            "fading_depth_db": self.fading_depth_db,
            "fading_rate_hz": self.fading_rate_hz,
            "selective_fading": self.selective_fading,
            "qrm": self.qrm,
            "bandpass_low_hz": self.bandpass_low_hz,
            "bandpass_high_hz": self.bandpass_high_hz,
            "squelch_tail_ms": self.squelch_tail_ms,
            "jammed": self.jammed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BandConditions":
        """Reconstruct stored conditions for AAR Dirty re-render (spec §4.5)."""
        return cls(
            freq_hz=float(d["freq_hz"]),
            region=BandRegion(d["region"]),
            snr_db=float(d["snr_db"]),
            pink_weight=float(d["pink_weight"]),
            fading_depth_db=float(d["fading_depth_db"]),
            fading_rate_hz=float(d["fading_rate_hz"]),
            selective_fading=bool(d["selective_fading"]),
            qrm=bool(d["qrm"]),
            bandpass_low_hz=float(d["bandpass_low_hz"]),
            bandpass_high_hz=float(d["bandpass_high_hz"]),
            squelch_tail_ms=float(d["squelch_tail_ms"]),
            jammed=bool(d.get("jammed", False)),
        )


@dataclass(frozen=True)
class CurveAnchor:
    """One editable point on the noise-vs-frequency curve (spec §4.1)."""

    freq_hz: float
    snr_db: float
    fading_depth_db: float
    fading_rate_hz: float


# Default anchors approximating the spec §4.1 table. Interpolation is done in
# log-frequency space so the three-decade span feels natural when tuning.
DEFAULT_ANCHORS: tuple[CurveAnchor, ...] = (
    CurveAnchor(1_600_000.0, snr_db=6.0, fading_depth_db=22.0, fading_rate_hz=0.30),
    CurveAnchor(5_000_000.0, snr_db=8.0, fading_depth_db=20.0, fading_rate_hz=0.35),
    CurveAnchor(10_000_000.0, snr_db=12.0, fading_depth_db=16.0, fading_rate_hz=0.45),
    CurveAnchor(20_000_000.0, snr_db=16.0, fading_depth_db=11.0, fading_rate_hz=0.60),
    CurveAnchor(30_000_000.0, snr_db=20.0, fading_depth_db=7.0, fading_rate_hz=0.90),
    CurveAnchor(100_000_000.0, snr_db=25.0, fading_depth_db=3.0, fading_rate_hz=2.0),
    CurveAnchor(300_000_000.0, snr_db=28.0, fading_depth_db=1.5, fading_rate_hz=3.0),
    CurveAnchor(1_000_000_000.0, snr_db=31.0, fading_depth_db=0.7, fading_rate_hz=4.0),
    CurveAnchor(3_000_000_000.0, snr_db=34.0, fading_depth_db=0.3, fading_rate_hz=5.0),
)


@dataclass
class JammingSpan:
    """An instructor-injected jamming region (spec §3.1.5, §4.2)."""

    low_hz: float
    high_hz: float
    intensity: float = 1.0  # multiplies the jam noise; 1.0 = heavy

    def covers(self, freq_hz: float) -> bool:
        return self.low_hz <= freq_hz <= self.high_hz


@dataclass
class BandProfile:
    """The single active band profile (spec §5.1 ``band_profile`` row).

    Holds the editable curve anchors, the global atmospheric multiplier, and any
    active jamming spans, and resolves them into :class:`BandConditions` for a
    given frequency. The atmospheric multiplier scales the *severity*: values
    > 1 worsen conditions (lower SNR, deeper fading) to simulate a bad HF day;
    values < 1 improve them.
    """

    anchors: list[CurveAnchor] = field(default_factory=lambda: list(DEFAULT_ANCHORS))
    atmospheric_multiplier: float = 1.0
    jamming: list[JammingSpan] = field(default_factory=list)
    crypto_delay_ms: int = 1500
    crypto_enabled: bool = True

    def __post_init__(self) -> None:
        self.anchors = sorted(self.anchors, key=lambda a: a.freq_hz)
        if len(self.anchors) < 2:
            raise ValueError("band curve needs at least two anchors")

    # -- interpolation ----------------------------------------------------- #

    def _interp(self, freq_hz: float) -> CurveAnchor:
        """Interpolate the curve at ``freq_hz`` (log-frequency, linear value)."""
        freq_hz = clamp_frequency(freq_hz)
        freqs = [a.freq_hz for a in self.anchors]
        i = bisect.bisect_left(freqs, freq_hz)
        if i == 0:
            return self.anchors[0]
        if i >= len(self.anchors):
            return self.anchors[-1]
        lo, hi = self.anchors[i - 1], self.anchors[i]
        # Position in log space between the two surrounding anchors.
        t = (math.log10(freq_hz) - math.log10(lo.freq_hz)) / (
            math.log10(hi.freq_hz) - math.log10(lo.freq_hz)
        )

        def lerp(a: float, b: float) -> float:
            return a + (b - a) * t

        return CurveAnchor(
            freq_hz=freq_hz,
            snr_db=lerp(lo.snr_db, hi.snr_db),
            fading_depth_db=lerp(lo.fading_depth_db, hi.fading_depth_db),
            fading_rate_hz=lerp(lo.fading_rate_hz, hi.fading_rate_hz),
        )

    # -- resolution -------------------------------------------------------- #

    def conditions_at(self, freq_hz: float) -> BandConditions:
        """Resolve full audio conditions at ``freq_hz`` for this instant."""
        freq_hz = clamp_frequency(freq_hz)
        region = region_for(freq_hz)
        base = self._interp(freq_hz)

        # Atmospheric multiplier worsens (>1) or improves (<1) the curve. It
        # subtracts from SNR proportionally and scales fading depth up.
        mult = max(0.0, self.atmospheric_multiplier)
        # Reference SNR span ~ 6..34 dB; worsening removes up to the headroom.
        snr = base.snr_db - (mult - 1.0) * 8.0
        fading_depth = base.fading_depth_db * mult

        is_hf = region in (BandRegion.LOW_HF, BandRegion.HIGH_HF)
        pink_weight = _pink_weight_for(freq_hz)

        jammed = any(j.covers(freq_hz) for j in self.jamming)
        if jammed:
            jam_intensity = max((j.intensity for j in self.jamming if j.covers(freq_hz)), default=1.0)
            # Jamming overrides the baseline with heavy noise on this frequency.
            snr = min(snr, 0.0) - 6.0 * jam_intensity

        return BandConditions(
            freq_hz=freq_hz,
            region=region,
            snr_db=snr,
            pink_weight=pink_weight,
            fading_depth_db=fading_depth,
            fading_rate_hz=base.fading_rate_hz,
            selective_fading=is_hf,
            qrm=is_hf,
            bandpass_low_hz=300.0 + (60.0 if region is BandRegion.LOW_HF else 0.0),
            bandpass_high_hz=3000.0 - (300.0 if region is BandRegion.LOW_HF else 0.0),
            squelch_tail_ms=_squelch_tail_for(region),
            jammed=jammed,
        )

    # -- serialisation ----------------------------------------------------- #

    def curve_to_json(self) -> list[dict]:
        return [
            {
                "freq_hz": a.freq_hz,
                "snr_db": a.snr_db,
                "fading_depth_db": a.fading_depth_db,
                "fading_rate_hz": a.fading_rate_hz,
            }
            for a in self.anchors
        ]

    @classmethod
    def from_curve_json(cls, data: list[dict], **kwargs) -> "BandProfile":
        anchors = [
            CurveAnchor(
                freq_hz=float(d["freq_hz"]),
                snr_db=float(d["snr_db"]),
                fading_depth_db=float(d["fading_depth_db"]),
                fading_rate_hz=float(d["fading_rate_hz"]),
            )
            for d in data
        ]
        return cls(anchors=anchors, **kwargs)

    def with_atmospheric(self, multiplier: float) -> "BandProfile":
        return replace(self, atmospheric_multiplier=multiplier)


def _pink_weight_for(freq_hz: float) -> float:
    """Pink-noise dominance: strongest at low HF, fading toward UHF (§4.1.1)."""
    lo, hi = math.log10(MIN_FREQ_HZ), math.log10(MAX_FREQ_HZ)
    t = (math.log10(clamp_frequency(freq_hz)) - lo) / (hi - lo)
    return max(0.0, min(1.0, 1.0 - t))


def _squelch_tail_for(region: BandRegion) -> float:
    return {
        BandRegion.LOW_HF: 220.0,
        BandRegion.HIGH_HF: 160.0,
        BandRegion.VHF: 90.0,
        BandRegion.UHF: 60.0,
    }[region]
