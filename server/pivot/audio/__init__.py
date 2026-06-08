"""Audio plane: recording tap, WebRTC router, and per-listener render loop.

* :mod:`pivot.audio.recording` — writes the clean, pre-DSP per-station WAV that
  is the single source of audio truth (spec §3.5.1, §4.5).
* :mod:`pivot.audio.render` — turns a stored event back into audio for AAR
  Clean/Dirty playback (§3.6.3, §4.5).
* :mod:`pivot.audio.router` — the aiortc WebRTC audio router and per-listener
  mixer (spec §6.3, Appendix A). Requires the ``audio`` extra; imported lazily.
"""
