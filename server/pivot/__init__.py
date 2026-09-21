"""PIVOT — Procedural Interactive Voice Operations Trainer (Tactical).

A self-hosted, LAN-only radio voice-procedure trainer. This package is the
headless server runtime: FastAPI API + WebSocket signalling, the WebRTC audio
router, the DSP engine, faster-whisper transcription, and the SQLite data layer.
Both the instructor (password-authenticated) and the trainees connect from a web
browser over the LAN.

See the top-level repository ``README.md`` and ``ROADMAP.md`` for how the
modules map onto the software specification (PIVOT Spec v1.6).
"""

from pivot.version import __version__ as __version__
from pivot.version import version_info as version_info

__all__ = ["__version__", "version_info"]
