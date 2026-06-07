"""faster-whisper backend (spec §3.1.6, §3.5.2).

Wraps ``faster_whisper.WhisperModel`` (MIT, CTranslate2 inference). Imported
lazily by the worker so the package is only required when transcription actually
runs (the ``transcription`` extra). Average word confidence is derived from the
segment/word probabilities so the AAR can amber-flag low-confidence events.
"""

from __future__ import annotations

import math

import numpy as np

from pivot.transcription.worker import TranscriptionResult


class FasterWhisperTranscriber:
    """Lazy, configurable faster-whisper wrapper."""

    def __init__(
        self,
        model_size: str = "small",
        compute_type: str = "auto",
        device: str = "auto",
        beam_size: int = 5,
    ) -> None:
        self.model_size = model_size
        self.compute_type = compute_type
        self.device = device
        self.beam_size = beam_size
        self._model = None

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # lazy import (extra)

            self._model = WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type
            )
        return self._model

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int,
        *,
        language: str = "en",
        initial_prompt: str = "",
    ) -> TranscriptionResult:
        model = self._load()
        # faster-whisper expects 16 kHz mono float32.
        mono = np.asarray(audio, dtype=np.float32).reshape(-1)
        segments, _info = model.transcribe(
            mono,
            language=language or None,
            initial_prompt=initial_prompt or None,
            beam_size=self.beam_size,
            word_timestamps=True,
        )

        texts: list[str] = []
        probs: list[float] = []
        for seg in segments:
            texts.append(seg.text)
            words = getattr(seg, "words", None)
            if words:
                probs.extend(w.probability for w in words if w.probability is not None)
            elif getattr(seg, "avg_logprob", None) is not None:
                probs.append(math.exp(seg.avg_logprob))

        text = " ".join(t.strip() for t in texts).strip()
        confidence = float(np.mean(probs)) if probs else 0.0
        return TranscriptionResult(text=text, confidence=confidence)
