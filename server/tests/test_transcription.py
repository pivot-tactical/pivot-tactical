"""Tests for the async transcription worker (spec §3.5.2)."""

import numpy as np
import pytest

from pivot.db import repository as repo
from pivot.db.config_store import ConfigStore
from pivot.db.models import TranscriptionStatus
from pivot.runtime.manager import SessionManager
from pivot.transcription.worker import TranscriptionResult, TranscriptionWorker


class FakeTranscriber:
    def __init__(self, text="SITREP FOLLOWS", confidence=0.92):
        self.text = text
        self.confidence = confidence
        self.calls = []

    def transcribe(self, audio, sample_rate, *, language, initial_prompt):
        self.calls.append(
            {"language": language, "initial_prompt": initial_prompt, "samples": len(audio)}
        )
        return TranscriptionResult(text=self.text, confidence=self.confidence)


@pytest.fixture
def manager(database, settings):
    return SessionManager(database, settings)


def tone(seconds=1.0, sr=16000):
    t = np.arange(int(seconds * sr)) / sr
    return (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def _make_event(manager, audio):
    manager.start_session("EX")
    manager.login("ALPHA", "t-1")
    manager.login("BRAVO", "t-2")
    manager.tune("t-1", "14.250 MHz")
    manager.tune("t-2", "14.250 MHz")
    manager.ptt_start("t-1")
    return manager.ptt_end("t-1", audio=audio)["event_id"]


def test_worker_transcribes_event(database, settings, manager):
    event_id = _make_event(manager, tone(1.0))
    fake = FakeTranscriber("SITREP FOLLOWS OVER", 0.93)
    worker = TranscriptionWorker(database, settings, transcriber=fake)
    status = worker.process_event(event_id)
    assert status is TranscriptionStatus.DONE
    with database.session() as s:
        e = repo.get_event(s, event_id)
        assert e.transcription == "SITREP FOLLOWS OVER"
        assert e.transcription_confidence == pytest.approx(0.93)


def test_worker_skips_short_events(database, settings, manager):
    # Default skip threshold is 0.5s; a 0.2s clip is skipped.
    event_id = _make_event(manager, tone(0.2))
    worker = TranscriptionWorker(database, settings, transcriber=FakeTranscriber())
    assert worker.process_event(event_id) is TranscriptionStatus.SKIPPED
    with database.session() as s:
        assert repo.get_event(s, event_id).transcription_status is TranscriptionStatus.SKIPPED


def test_worker_uses_initial_prompt_from_config(database, settings, manager):
    with database.session() as s:
        cfg = ConfigStore(s)
        cfg.set("whisper_initial_prompt", "Use NATO phonetics")
        cfg.set("whisper_custom_vocabulary", ["SUNRAY", "WILCO"])
    event_id = _make_event(manager, tone(1.0))
    fake = FakeTranscriber()
    TranscriptionWorker(database, settings, transcriber=fake).process_event(event_id)
    prompt = fake.calls[0]["initial_prompt"]
    assert "NATO" in prompt and "SUNRAY" in prompt and "WILCO" in prompt


def test_low_confidence_is_stored_for_amber_flagging(database, settings, manager):
    event_id = _make_event(manager, tone(1.0))
    fake = FakeTranscriber("garbled", confidence=0.55)
    TranscriptionWorker(database, settings, transcriber=fake).process_event(event_id)
    with database.session() as s:
        e = repo.get_event(s, event_id)
        # Below the default 0.80 threshold -> AAR renders amber.
        assert e.transcription_confidence < 0.80
        assert e.transcription_status is TranscriptionStatus.DONE


def test_aborted_event_is_still_transcribed(database, settings, manager):
    """Crypto-sync-aborted attempts are recorded AND transcribed (§3.5.1)."""
    manager.start_session("EX")
    manager.login("ALPHA", "t-1")
    from pivot.core.crypto import RadioMode

    manager.set_mode("t-1", RadioMode.CYPHER)
    manager.ptt_start("t-1")
    event = manager.ptt_abort("t-1", audio=tone(1.0))
    worker = TranscriptionWorker(database, settings, transcriber=FakeTranscriber())
    assert worker.process_event(event["event_id"]) is TranscriptionStatus.DONE


def test_manager_enqueues_when_worker_attached(database, settings, manager):
    enqueued = []
    manager.transcription_worker = type(
        "W", (), {"enqueue": lambda self, eid: enqueued.append(eid)}
    )()
    _make_event(manager, tone(1.0))
    assert len(enqueued) == 1
