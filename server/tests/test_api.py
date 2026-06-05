"""Tests for the REST + WebSocket API (spec §6)."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from pivot.api.app import create_app
from pivot.api.deps import require_local


@pytest.fixture
def client(settings):
    app = create_app(settings)
    # TestClient reports host "testclient"; treat admin routes as local in tests.
    app.dependency_overrides[require_local] = lambda: None
    with TestClient(app) as c:
        yield c


def test_status_endpoint(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "PIVOT"
    assert "version" in body


def test_login_and_tune_and_mode(client):
    r = client.post("/api/login", json={"name": "ALPHA"})
    assert r.status_code == 200
    login = r.json()
    rid = login["radio_id"]

    r = client.post("/api/radio/tune", json={"radio_id": rid, "frequency": "14.250 MHz"})
    assert r.status_code == 200
    assert r.json()["band_region"] == "High HF"

    r = client.post("/api/radio/mode", json={"radio_id": rid, "mode": "Cypher"})
    assert r.status_code == 200
    assert r.json()["mode"] == "Cypher"


def test_tune_unknown_radio_404(client):
    r = client.post("/api/radio/tune", json={"radio_id": "nope", "frequency": "14.0"})
    assert r.status_code == 404


def test_band_profile_endpoint(client):
    r = client.get("/api/band-profile")
    assert r.status_code == 200
    assert "curve" in r.json() and "atmospheric_multiplier" in r.json()


def test_admin_terminals_and_session(client):
    assert client.post("/api/admin/session/start", json={"name": "EX"}).status_code == 200
    r = client.get("/api/admin/terminals")
    assert r.status_code == 200
    assert r.json()["session_active"] is True


def test_admin_scenario(client):
    r = client.post("/api/admin/scenario", json={"atmospheric_multiplier": 2.0,
                                                  "jamming_on": [[14_200_000, 14_300_000]]})
    assert r.status_code == 200
    assert r.json()["atmospheric_multiplier"] == 2.0
    assert r.json()["jamming"] == [[14_200_000, 14_300_000]]


def test_local_only_guard_blocks_remote(settings):
    # Without the override, admin endpoints must reject non-loopback callers.
    app = create_app(settings)
    with TestClient(app) as c:
        assert c.get("/api/admin/terminals").status_code == 403


def test_sessions_events_and_export(client, settings):
    # Drive the manager directly to create a session + event, then hit REST.
    manager = client.app.state.manager
    manager.start_session("EX-EXPORT")
    manager.login("ALPHA", "t-1")
    manager.login("BRAVO", "t-2")
    manager.tune("t-1", "14.250 MHz")
    manager.tune("t-2", "14.250 MHz")
    manager.ptt_start("t-1")
    t = np.sin(2 * np.pi * 440 * np.arange(8000) / 16000).astype(np.float32)
    event = manager.ptt_end("t-1", audio=t)
    session_id = manager.current_session_id

    r = client.get("/api/sessions")
    assert r.status_code == 200 and any(s["id"] == session_id for s in r.json())

    r = client.get(f"/api/sessions/{session_id}/events")
    assert r.status_code == 200 and len(r.json()) == 1

    # Clean audio stream.
    r = client.get(f"/api/events/{event['event_id']}/audio?mode=clean")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert r.content[:4] == b"RIFF"

    # Dirty re-render stream.
    r = client.get(f"/api/events/{event['event_id']}/audio?mode=dirty&view=cypher")
    assert r.status_code == 200 and r.content[:4] == b"RIFF"

    # ZIP export contains logs.
    r = client.post(f"/api/sessions/{session_id}/export?fmt=zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.content[:2] == b"PK"

    # CSV export.
    r = client.post(f"/api/sessions/{session_id}/export?fmt=csv")
    assert r.status_code == 200 and "trainee_name" in r.text


def test_websocket_login_tune_and_ptt(client):
    client.post("/api/admin/session/start", json={"name": "WS-EX"})
    with client.websocket_connect("/ws?name=CHARLIE&trainee_id=ws-1") as wsconn:
        welcome = wsconn.receive_json()
        assert welcome["type"] == "welcome"
        profile = wsconn.receive_json()
        assert profile["type"] == "band_profile_update"

        wsconn.send_json({"type": "tune", "payload": {"frequency": "30.100 MHz"}})
        # Drain until we see the 'tuned' ack (terminal_update broadcasts interleave).
        ack = _recv_until(wsconn, "tuned")
        assert ack["payload"]["band_region"] == "VHF"

        wsconn.send_json({"type": "mode_change", "payload": {"mode": "Cypher"}})
        ack = _recv_until(wsconn, "mode_changed")
        assert ack["payload"]["mode"] == "Cypher"

        wsconn.send_json({"type": "heartbeat", "payload": {}})
        assert _recv_until(wsconn, "heartbeat")["type"] == "heartbeat"


def test_websocket_plain_ptt_creates_event(client):
    client.post("/api/admin/session/start", json={"name": "WS-PTT"})
    manager = client.app.state.manager
    # A listener so the TX is 'Heard'.
    manager.login("LISTENER", "listener-1")
    manager.tune("listener-1", "145.500 MHz")

    with client.websocket_connect("/ws?name=DELTA&trainee_id=ws-2") as wsconn:
        wsconn.receive_json()  # welcome
        wsconn.receive_json()  # band profile
        wsconn.send_json({"type": "ptt_start", "payload": {"frequency": "145.500 MHz",
                                                           "tx_mode": "Plain"}})
        started = _recv_until(wsconn, "ptt_started")
        assert started["payload"]["sync_applies"] is False
        wsconn.send_json({"type": "ptt_end", "payload": {}})
        ended = _recv_until(wsconn, "ptt_ended")
        assert ended["payload"]["audibility"] == "Heard"


def _recv_until(wsconn, mtype, limit=20):
    for _ in range(limit):
        msg = wsconn.receive_json()
        if msg["type"] == mtype:
            return msg
    raise AssertionError(f"did not receive {mtype!r} within {limit} messages")
