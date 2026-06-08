"""Asynchronous transcription worker (spec §3.5.2)."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from pivot.audio.recording import duration_ms, event_audio_path, read_recording
from pivot.config import Settings
from pivot.db import repository as repo
from pivot.db.config_store import ConfigStore
from pivot.db.database import Database
from pivot.db.models import TranscriptionStatus

log = logging.getLogger("pivot.transcription")


@dataclass
class TranscriptionResult:
    text: str
    confidence: float  # average word confidence, 0..1


class Transcriber(Protocol):
    """A speech-to-text backend. ``FasterWhisperTranscriber`` is the default;
    tests inject a fake."""

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int,
        *,
        language: str,
        initial_prompt: str,
    ) -> TranscriptionResult: ...


class TranscriptionWorker:
    """Drains finished events and transcribes them off the live path."""

    def __init__(
        self,
        db: Database,
        settings: Settings,
        transcriber: Transcriber | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self._injected_transcriber = transcriber is not None
        self._transcriber = transcriber
        self._transcriber_key: tuple[str, str] | None = None
        self._queue: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        # Called with the event_id after each event is processed (any outcome),
        # so the instructor's live log can refresh that row (§3.5.2).
        self.on_complete: Callable[[str], None] | None = None

    # -- lifecycle --------------------------------------------------------- #

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="pivot-transcribe")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put_nowait("")  # unblock
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def enqueue(self, event_id: str) -> None:
        self._queue.put_nowait(event_id)

    def _run(self) -> None:  # pragma: no cover - thread loop exercised via process_event
        while not self._stop.is_set():
            event_id = self._queue.get()
            if not event_id or self._stop.is_set():
                continue
            try:
                self.process_event(event_id)
            except Exception:  # never let one bad event kill the worker
                log.exception("transcription failed for %s", event_id)

    # -- core (synchronous, directly testable) ----------------------------- #

    def process_event(self, event_id: str) -> TranscriptionStatus:
        """Transcribe one event, persist the result, and notify listeners."""
        status = self._process(event_id)
        if self.on_complete is not None:
            try:
                self.on_complete(event_id)
            except Exception:  # a listener error must not affect transcription
                log.exception("on_complete callback failed for %s", event_id)
        return status

    def _process(self, event_id: str) -> TranscriptionStatus:
        with self.db.session() as s:
            cfg = ConfigStore(s)
            language = str(cfg.get("whisper_language", "en"))
            initial_prompt = self._build_prompt(cfg)
            skip_under = float(cfg.get("transcription_skip_under_seconds", 0.5))
            whisper_model = str(cfg.get("whisper_model", "small"))
            whisper_compute_type = str(cfg.get("whisper_compute_type", "auto"))
            event = repo.get_event(s, event_id)
            if event is None:
                return TranscriptionStatus.FAILED
            audio_path = event_audio_path(
                self.settings.recordings_dir, event.session_id, event_id
            )

        if not audio_path.exists():
            self._store(event_id, None, None, TranscriptionStatus.FAILED)
            return TranscriptionStatus.FAILED

        audio, sr = read_recording(audio_path)
        if duration_ms(audio, sr) < skip_under * 1000:
            # Skip events shorter than N seconds (§3.1.6 toggle).
            self._store(event_id, None, None, TranscriptionStatus.SKIPPED)
            return TranscriptionStatus.SKIPPED

        try:
            transcriber = self._get_transcriber(whisper_model, whisper_compute_type)
            result = transcriber.transcribe(
                audio, sr, language=language, initial_prompt=initial_prompt
            )
        except Exception:
            log.exception("transcriber error for %s", event_id)
            self._store(event_id, None, None, TranscriptionStatus.FAILED)
            return TranscriptionStatus.FAILED

        self._store(event_id, result.text, result.confidence, TranscriptionStatus.DONE)
        return TranscriptionStatus.DONE

    # -- helpers ----------------------------------------------------------- #

    def _store(self, event_id, text, confidence, status) -> None:
        with self.db.session() as s:
            repo.set_transcription(
                s, event_id, text_value=text, confidence=confidence, status=status
            )

    def _build_prompt(self, cfg: ConfigStore) -> str:
        """Bias decoding toward callsigns/prowords (§10 mitigation)."""
        base = str(cfg.get("whisper_initial_prompt", "") or "")
        vocab = cfg.get("whisper_custom_vocabulary", []) or []
        if vocab:
            joined = ", ".join(str(v) for v in vocab)
            base = (base + " " + joined).strip()
        return base

    def _get_transcriber(self, model: str, compute_type: str) -> Transcriber:
        """Build (or rebuild) the backend, picking up Settings changes live.

        Without this, a model/compute-type change made after the first failed
        attempt (e.g. switching off an unsupported ``int8`` variant) would
        silently keep using the original — already-broken — instance until the
        whole server restarted (§3.1.6).
        """
        if self._injected_transcriber:
            return self._transcriber

        key = (model, compute_type)
        if self._transcriber is None or key != self._transcriber_key:
            from pivot.transcription.whisper import FasterWhisperTranscriber

            self._transcriber = FasterWhisperTranscriber(model_size=model, compute_type=compute_type)
            self._transcriber_key = key
        return self._transcriber
