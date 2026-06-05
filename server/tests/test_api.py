"""Tests for the REST + WebSocket API (spec §6)."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from pivot.api.app import create_app
from pivot.api.deps import require_instructor
from pivot.auth import DEFAULT_INSTRUCTOR_PASSWORD
from pivot.db.config_store import ConfigStore


@pytest.fixture
def client(settings):
    """Client with instructor auth bypassed (admin routes reachable)."""
    app = create_app(settings)
    app.dependency_overrides[require_instructor] = lambda: None
    with TestClient(app) as c:
        yield c


@pytest.fixture
def raw_client(settings):
    """Client with auth enforced, for testing the auth boundary itself."""
    app = create_app(settings)
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


def test_admin_requires_instructor_token(raw_client):
    # Without a valid instructor token, admin endpoints reject the caller.
    assert raw_client.get("/api/admin/terminals").status_code == 401


def test_instructor_login_and_authenticated_admin(raw_client):
    # Wrong password is rejected.
    bad = raw_client.post("/api/login", json={"role": "instructor", "password": "nope"})
    assert bad.status_code == 401

    # Default password logs in and returns a bearer token + change-me flag.
    r = raw_client.post("/api/login", json={"role": "instructor",
                                            "password": DEFAULT_INSTRUCTOR_PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "instructor"
    assert body["must_change_password"] is True
    token = body["token"]

    # The token authorises admin endpoints.
    headers = {"Authorization": f"Bearer {token}"}
    assert raw_client.get("/api/admin/terminals", headers=headers).status_code == 200


def test_change_password_and_relogin(raw_client):
    token = raw_client.post(
        "/api/login", json={"role": "instructor", "password": DEFAULT_INSTRUCTOR_PASSWORD}
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Change the password; the old token is invalidated.
    r = raw_client.post("/api/admin/password",
                        json={"current_password": DEFAULT_INSTRUCTOR_PASSWORD,
                              "new_password": "range-safety"}, headers=headers)
    assert r.status_code == 200
    assert raw_client.get("/api/admin/terminals", headers=headers).status_code == 401

    # New password works and no longer flags must_change_password.
    r = raw_client.post("/api/login", json={"role": "instructor", "password": "range-safety"})
    assert r.status_code == 200 and r.json()["must_change_password"] is False


def test_trainee_cannot_operate_instructor_radio(client):
    # Create an instructor radio, then attempt to tune it via the open trainee
    # endpoint — rejected regardless of auth (the guard is in the endpoint).
    radio = client.post("/api/admin/instructor-radios",
                        json={"frequency": "30.000 MHz"}).json()
    r = client.post("/api/radio/tune",
                    json={"radio_id": radio["radio_id"], "frequency": "31.0 MHz"})
    assert r.status_code == 403


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


_FAKE_RELEASES = [
    {"tag_name": "1.1.0", "name": "1.1.0", "prerelease": False, "assets": [
        {"name": "PIVOT-Tactical-v1.1.0-win64.zip", "browser_download_url": "http://x/w"}]},
    {"tag_name": "1.2.0-rc.1", "name": "rc", "prerelease": True, "assets": [
        {"name": "PIVOT-Tactical-v1.2.0-rc.1-win64.zip", "browser_download_url": "http://x/r"}]},
    {"tag_name": "0.9.0", "name": "old", "prerelease": False, "assets": []},
]


def test_update_check_respects_channel(client, monkeypatch):
    monkeypatch.setattr("pivot.updates.github.fetch_releases", lambda *a, **k: _FAKE_RELEASES)
    with client.app.state.manager.db.session() as s:
        ConfigStore(s).set("github_repo", "pivot-tactical/pivot-tactical")

    # Stable channel: prerelease excluded, only 1.1.0 is an available update.
    r = client.get("/api/admin/updates/check").json()
    assert r["reachable"] is True and r["current_version"] == "1.0.0"
    assert [a["tag"] for a in r["available"]] == ["1.1.0"]

    # Include prereleases: the rc shows up too (newest first).
    with client.app.state.manager.db.session() as s:
        ConfigStore(s).set("update_channel", "include_prereleases")
    r = client.get("/api/admin/updates/check").json()
    assert [a["tag"] for a in r["available"]] == ["1.2.0-rc.1", "1.1.0"]


def test_update_check_graceful_when_unreachable(client, monkeypatch):
    def boom(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr("pivot.updates.github.fetch_releases", boom)
    r = client.get("/api/admin/updates/check").json()
    assert r["reachable"] is False and r["available"] == []


def test_event_audio_404_when_no_recording(client):
    # An event logged without captured audio (no voice transport) has no WAV on
    # disk; playback must 404 gracefully, not 500.
    manager = client.app.state.manager
    manager.start_session("EX")
    manager.login("ALPHA", "t-1")
    manager.login("BRAVO", "t-2")
    manager.tune("t-1", "14.250 MHz")
    manager.tune("t-2", "14.250 MHz")
    manager.ptt_start("t-1")
    event = manager.ptt_end("t-1")  # no audio argument -> no recording file
    r = client.get(f"/api/events/{event['event_id']}/audio?mode=clean")
    assert r.status_code == 404


def test_instructor_websocket_controls_radio(client):
    # WS uses the real AuthService token (the require_instructor override only
    # affects REST). Issue a token and add an instructor radio to drive.
    token = client.app.state.auth.issue_token()
    radio = client.post("/api/admin/instructor-radios", json={"frequency": "40.000 MHz"}).json()

    with client.websocket_connect(f"/ws?token={token}") as wsconn:
        welcome = wsconn.receive_json()
        assert welcome["type"] == "welcome" and welcome["payload"]["role"] == "instructor"

        wsconn.send_json({"type": "instr_tune",
                          "payload": {"radio_id": radio["radio_id"], "frequency": "41.000 MHz"}})
        ack = _recv_until(wsconn, "tuned")
        assert "41.000" in ack["payload"]["frequency"]


def test_instructor_websocket_rejects_unauthenticated_control(client):
    # No token -> trainee session; instr_* messages are unknown to it.
    with client.websocket_connect("/ws?name=NOPE&trainee_id=x") as wsconn:
        wsconn.receive_json()  # welcome (trainee)
        wsconn.receive_json()  # band profile
        wsconn.send_json({"type": "instr_tune", "payload": {"radio_id": "instr-1"}})
        assert _recv_until(wsconn, "error")["payload"]["detail"].startswith("unknown")


def test_websocket_audio_frame_is_recorded(client):
    # A binary PCM frame sent while keyed is tapped for the recording, so the
    # event ends with non-zero duration and a WAV on disk.
    from pivot.audio.pcm import float32_to_pcm16

    client.post("/api/admin/session/start", json={"name": "AUDIO"})
    with client.websocket_connect("/ws?name=TX&trainee_id=tx-audio") as wsconn:
        wsconn.receive_json()  # welcome
        wsconn.receive_json()  # band profile
        wsconn.send_json({"type": "ptt_start",
                          "payload": {"frequency": "145.500 MHz", "tx_mode": "Plain"}})
        _recv_until(wsconn, "ptt_started")
        frame = float32_to_pcm16(
            (0.2 * np.sin(2 * np.pi * 440 * np.arange(1600) / 16000)).astype(np.float32)
        )
        wsconn.send_bytes(frame)
        wsconn.send_json({"type": "ptt_end", "payload": {}})
        ended = _recv_until(wsconn, "ptt_ended")
        assert ended["payload"]["duration_ms"] > 0


def _recv_until(wsconn, mtype, limit=20):
    for _ in range(limit):
        msg = wsconn.receive_json()
        if msg["type"] == mtype:
            return msg
    raise AssertionError(f"did not receive {mtype!r} within {limit} messages")
