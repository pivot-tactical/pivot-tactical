"""Server-side transcription (spec §3.1.6, §3.5.2).

faster-whisper runs on the clean (pre-DSP) per-station audio to maximise
accuracy. Transcription is asynchronous and never blocks live audio: a worker
thread drains a queue of finished events, transcribes each one, and writes the
text + average word confidence back to the event row. Events below the confidence
threshold are flagged amber in the AAR (§3.6.2).

The faster-whisper backend (MIT, §13.3) is imported lazily so the core install
and CI run without the model/runtime present; tests inject a fake transcriber.
"""

from pivot.transcription.worker import (
    Transcriber,
    TranscriptionResult,
    TranscriptionWorker,
)

__all__ = ["TranscriptionResult", "TranscriptionWorker", "Transcriber"]
