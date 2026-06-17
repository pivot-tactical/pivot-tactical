"""Tests for the per-listener render grouping/mixing (spec §6.3, Appendix A)."""

import numpy as np

from pivot.audio.mixer import distinct_renders, group_renders, render_net_frame
from pivot.core.bands import BandProfile
from pivot.core.crypto import Reception
from pivot.dsp.engine import DspEngine

SR = 16000


def test_group_renders_collapses_identical_receptions():
    """Fan-out: listeners with the same reception share one stream (A.4)."""
    render_map = {
        "p1": Reception.HASH,
        "p2": Reception.HASH,
        "c1": Reception.CLEAR,
        "c2": Reception.CLEAR,
        "c3": Reception.CLEAR,
        "tx": Reception.SILENCE,
    }
    groups = group_renders(render_map)
    assert set(groups[Reception.HASH]) == {"p1", "p2"}
    assert set(groups[Reception.CLEAR]) == {"c1", "c2", "c3"}
    # Five listeners, but only two distinct renders to compute.
    assert distinct_renders(render_map) == {Reception.HASH, Reception.CLEAR}


def test_render_net_frame_produces_one_buffer_per_reception():
    frame = (0.3 * np.sin(2 * np.pi * 440 * np.arange(SR // 50) / SR)).astype(np.float32)
    conditions = BandProfile().conditions_at(14e6)
    engine = DspEngine(SR)
    rendered = render_net_frame(
        {"tx": frame},
        conditions,
        {Reception.CLEAR, Reception.HASH},
        engine,
        rng=np.random.default_rng(0),
    )
    assert set(rendered) == {Reception.CLEAR, Reception.HASH}
    assert rendered[Reception.CLEAR].shape == frame.shape


def test_render_net_frame_collision():
    a = (0.3 * np.sin(2 * np.pi * 440 * np.arange(320) / SR)).astype(np.float32)
    b = (0.3 * np.sin(2 * np.pi * 660 * np.arange(320) / SR)).astype(np.float32)
    conditions = BandProfile().conditions_at(145e6)
    engine = DspEngine(SR)
    rendered = render_net_frame(
        {"a": a, "b": b},
        conditions,
        {Reception.PLAIN_COLLISION, Reception.CRYPTO_JAM},
        engine,
        rng=np.random.default_rng(0),
    )
    assert rendered[Reception.PLAIN_COLLISION].shape == a.shape
    assert rendered[Reception.CRYPTO_JAM].shape == a.shape


def test_render_net_frame_empty_when_no_transmitters():
    conditions = BandProfile().conditions_at(14e6)
    rendered = render_net_frame({}, conditions, {Reception.CLEAR}, DspEngine(SR))
    assert rendered == {}
