"""Crypto sync tone and PTT click/squelch transients (spec §4.3, §4.1.1).

The crypto sync tone is a short two-tone burst played **only to the transmitting
operator's own headset** while the radio waits out the crypto sync delay — it is
never broadcast on the net and is never re-rendered in AAR playback (§4.5).
"""

import numpy as np

# Named presets so settings can offer "KY-57 style" etc. (§4.3).
SYNC_TONE_PRESETS: dict[str, dict] = {
    "ky57": {"tones_hz": (1200.0, 1600.0), "duration_s": 0.30},
    "single": {"tones_hz": (1500.0,), "duration_s": 0.25},
    "sweep": {"tones_hz": (900.0, 1800.0), "duration_s": 0.35, "sweep": True},
}


def _fade_edges(x: np.ndarray, sample_rate: int, ms: float = 8.0) -> np.ndarray:
    """Apply short raised-cosine fades to avoid clicks at burst edges."""
    k = int(sample_rate * ms / 1000.0)
    if k <= 0 or 2 * k >= x.size:
        return x
    ramp = 0.5 * (1 - np.cos(np.linspace(0, np.pi, k)))
    x[:k] *= ramp
    x[-k:] *= ramp[::-1]
    return x


def crypto_sync_tone(
    sample_rate: int,
    preset: str = "ky57",
    level: float = 0.4,
) -> np.ndarray:
    """Synthesize the crypto sync tone burst (~0.3 s) for the given preset."""
    cfg = SYNC_TONE_PRESETS.get(preset, SYNC_TONE_PRESETS["ky57"])
    duration = float(cfg["duration_s"])
    n = int(sample_rate * duration)
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    t = np.arange(n) / sample_rate
    tones = cfg["tones_hz"]

    if cfg.get("sweep"):
        f0, f1 = tones[0], tones[-1]
        inst = f0 + (f1 - f0) * (t / duration)
        out = np.sin(2 * np.pi * np.cumsum(inst) / sample_rate)
    else:
        out = np.zeros(n, dtype=np.float64)
        for f in tones:
            out += np.sin(2 * np.pi * f * t)
        out /= max(len(tones), 1)

    out = _fade_edges(out.astype(np.float32), sample_rate)
    return (out * level).astype(np.float32)


def ptt_click(sample_rate: int, level: float = 0.5) -> np.ndarray:
    """A brief broadband click for PTT key-down/key-up (§4.1.1)."""
    n = max(1, int(sample_rate * 0.012))
    rng = np.random.default_rng(0)  # deterministic click
    click = rng.standard_normal(n).astype(np.float32)
    decay = np.exp(-np.linspace(0, 6, n)).astype(np.float32)
    return (click * decay * level).astype(np.float32)


def squelch_tail(sample_rate: int, tail_ms: float, level: float = 0.08) -> np.ndarray:
    """A short noise tail on key-up; longer on HF (§4.1.1)."""
    n = max(1, int(sample_rate * tail_ms / 1000.0))
    rng = np.random.default_rng(1)
    tail = rng.standard_normal(n).astype(np.float32)
    decay = np.exp(-np.linspace(0, 4, n)).astype(np.float32)
    return (tail * decay * level).astype(np.float32)
