"""Tests for the REST + WebSocket API (spec §6)."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from pivot.api.app import _maybe_start_transcription, create_app
from pivot.api.deps import require_instructor
from pivot.auth import DEFAULT_INSTRUCTOR_PASSWORD
from pivot.db import repository as repo
from pivot.db.config_store import ConfigStore
from pivot.version import version_info


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


def test_spa_path_traversal(client, tmp_path, monkeypatch):
    """Ensure the SPA fallback route resists path traversal attacks."""
    from pivot.api import app

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "assets").mkdir()
    (dist / "index.html").write_text("index")
    (dist / "app.js").write_text("js")

    # Create a file outside the dist dir to try and access
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    (secret_dir / "passwd").write_text("secret_content")

    monkeypatch.setattr(app, "frontend_dist_dir", lambda: dist)

    from fastapi.testclient import TestClient

    from pivot.api.app import create_app

    test_app = create_app()
    test_client = TestClient(test_app)

    # Normal access works
    assert test_client.get("/app.js").text == "js"

    # Path traversal fails and falls back to index.html
    # Traverse up and try to read secret/passwd
    assert test_client.get("/../secret/passwd").text == "index"
    assert test_client.get("/%2e%2e/secret/passwd").text == "index"


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
    assert r.json()["band_region"] == "HF"

    r = client.post("/api/radio/mode", json={"radio_id": rid, "mode": "Cypher"})
    assert r.status_code == 200
    assert r.json()["mode"] == "Cypher"


def test_tune_unknown_radio_404(client):
    r = client.post("/api/radio/tune", json={"radio_id": "nope", "frequency": "14.0"})
    assert r.status_code == 404


def test_band_profile_endpoint(client):
    r = client.get("/api/band-profile")
    assert r.status_code == 200
    assert "curve" in r.json() and "net_scenarios" in r.json()


def test_admin_terminals_and_session(client):
    assert client.post("/api/admin/session/start", json={"name": "EX"}).status_code == 200
    r = client.get("/api/admin/terminals")
    assert r.status_code == 200
    assert r.json()["session_active"] is True
    # The running scenario's name is exposed so the console can restore the box
    # after a refresh / restart (a resumed session has no broadcast to carry it).
    assert r.json()["session_name"] == "EX"


def test_admin_scenario(client):
    r = client.post("/api/admin/scenario", json={"jamming_on": [[14_200_000, 14_300_000]]})
    assert r.status_code == 200
    assert r.json()["jamming"] == [[14_200_000, 14_300_000]]


def test_admin_scenario_per_net(client):
    """Per-net interference/jam set from an instructor radio panel (§3.1.5)."""
    r = client.post("/api/admin/scenario", json={
        "net_scenario": {"frequency_hz": 14_250_000, "interference": 0.5, "jammed": True},
    })
    assert r.status_code == 200
    assert r.json()["net_scenarios"] == [
        {"freq_hz": 14_250_000.0, "interference": 0.5, "jammed": True}
    ]
    # The override is visible to trainee clients via the public band profile.
    assert client.get("/api/band-profile").json()["net_scenarios"][0]["jammed"] is True
    # Returning the net to defaults clears the override.
    r = client.post("/api/admin/scenario", json={
        "net_scenario": {"frequency_hz": 14_250_000, "interference": 0.0, "jammed": False},
    })
    assert r.json()["net_scenarios"] == []
    # Negative offsets (channel cleanup below baseline) are valid overrides.
    r = client.post("/api/admin/scenario", json={
        "net_scenario": {"frequency_hz": 14_250_000, "interference": -0.6},
    })
    assert r.json()["net_scenarios"] == [
        {"freq_hz": 14_250_000.0, "interference": -0.6, "jammed": False}
    ]


def test_admin_scenario_per_net_rejects_bad_level(client):
    r = client.post("/api/admin/scenario", json={
        "net_scenario": {"frequency_hz": 14_250_000, "interference": 1.5},
    })
    assert r.status_code == 422


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
    token = raw_client.cookies.get("pivot_token")

    # The token authorises admin endpoints.
    headers = {"Authorization": f"Bearer {token}"}
    assert raw_client.get("/api/admin/terminals", headers=headers).status_code == 200


def test_auth_refresh_slides_the_session(raw_client):
    r = raw_client.post("/api/login", json={"role": "instructor",
                                            "password": DEFAULT_INSTRUCTOR_PASSWORD})
    token = raw_client.cookies.get("pivot_token")
    headers = {"Authorization": f"Bearer {token}"}

    # A valid token can be refreshed for a fresh one that also authorises admin.
    refreshed = raw_client.post("/api/auth/refresh", headers=headers)
    assert refreshed.status_code == 200
    new_token = raw_client.cookies.get("pivot_token")
    assert new_token and new_token != token
    assert raw_client.get(
        "/api/admin/terminals", headers={"Authorization": f"Bearer {new_token}"}
    ).status_code == 200

    # Without a token it is rejected — the browser then shows the login.
    raw_client.cookies.clear()
    assert raw_client.post("/api/auth/refresh").status_code == 401
    assert raw_client.post(
        "/api/auth/refresh", headers={"Authorization": "Bearer bogus"}
    ).status_code == 401


def test_restart_unavailable_in_dev_run_mode(client):
    # No server host wired app.state.request_restart (TestClient) -> 503.
    r = client.post("/api/admin/restart", json={})
    assert r.status_code == 503


def test_restart_refused_during_session(client):
    client.app.state.manager.start_session("EX")
    r = client.post("/api/admin/restart", json={"force": False})
    assert r.status_code == 409


def test_restart_forced_during_session_when_wired(client):
    called: list[bool] = []
    client.app.state.request_restart = lambda: called.append(True)
    client.app.state.manager.start_session("EX")
    r = client.post("/api/admin/restart", json={"force": True})
    assert r.status_code == 200
    assert r.json()["restarting"] is True


def test_restart_ok_when_wired(client):
    client.app.state.request_restart = lambda: None
    r = client.post("/api/admin/restart", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["restarting"] is True
    assert "mode" in body


def test_rollback_without_retained_version_is_409(client):
    r = client.post("/api/admin/updates/rollback", json={})
    assert r.status_code == 409


def test_rollback_stages_retained_version(client, settings):
    # Seed a retained version in the side-by-side versions dir, then roll back.
    good = settings.versions_dir / "app-1.1.0"
    (good / "_internal").mkdir(parents=True)
    (good / "PIVOT-Tactical").write_text("retained 1.1.0")

    r = client.post("/api/admin/updates/rollback", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["rollback"] is True
    assert body["tag"] == "1.1.0"
    assert body["restart_required"] is True


def test_rollback_syncs_update_service_cache(client, settings):
    """Rolling back stages a retained version out-of-band; the background
    service's cached status (what the console streams over the WebSocket) must
    reflect it immediately, so the pane can't keep showing a version that was
    auto-staged before (§3.7.4)."""
    good = settings.versions_dir / "app-1.1.0"
    (good / "_internal").mkdir(parents=True)
    (good / "PIVOT-Tactical").write_text("retained 1.1.0")

    r = client.post("/api/admin/updates/rollback", json={})
    assert r.status_code == 200

    service = client.app.state.update_service
    assert service is not None
    assert service.snapshot()["staged_tag"] == "1.1.0"


def test_retained_versions_list_and_delete(client, settings):
    # Two versions kept on disk; the pane lists them with sizes and can delete.
    v1 = settings.versions_dir / "app-1.1.0"
    v1.mkdir(parents=True)
    (v1 / "app.bin").write_bytes(b"a" * 1000)
    v2 = settings.versions_dir / "app-1.0.0"
    v2.mkdir(parents=True)
    (v2 / "app.bin").write_bytes(b"b" * 10)

    listing = client.get("/api/admin/updates/retained")
    assert listing.status_code == 200
    tags = [d["tag"] for d in listing.json()["retained"]]
    assert tags == ["1.1.0", "1.0.0"]
    assert {d["tag"]: d["bytes"] for d in listing.json()["retained"]}["1.1.0"] == 1000

    gone = client.delete("/api/admin/updates/retained/1.0.0")
    assert gone.status_code == 200
    assert [d["tag"] for d in gone.json()["retained"]] == ["1.1.0"]
    assert not v2.exists()

    # Deleting something that isn't there is a 404, not a silent success.
    assert client.delete("/api/admin/updates/retained/9.9.9").status_code == 404


def test_change_password_and_relogin(raw_client):
    login_res = raw_client.post(
        "/api/login", json={"role": "instructor", "password": DEFAULT_INSTRUCTOR_PASSWORD}
    )
    token = raw_client.cookies.get("pivot_token")
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

    # Text export.
    r = client.post(f"/api/sessions/{session_id}/export?fmt=text")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert "ALPHA" in r.text

    # Invalid format.
    r = client.post(f"/api/sessions/{session_id}/export?fmt=pdf")
    assert r.status_code == 422


def test_edit_transcription_over_rest(client, settings):
    """POST /events/{id}/transcription corrects the text, preserves the machine
    transcription for diffing, and flags the row (§3.5.3)."""
    manager = client.app.state.manager
    manager.start_session("EX-EDIT")
    manager.login("ALPHA", "t-1")
    manager.tune("t-1", "14.250 MHz")
    manager.ptt_start("t-1")
    t = np.sin(2 * np.pi * 440 * np.arange(8000) / 16000).astype(np.float32)
    event = manager.ptt_end("t-1", audio=t)
    eid = event["event_id"]

    # Seed a (low-confidence) machine transcription, then correct it.
    with manager.db.session() as s:
        from pivot.db.models import TranscriptionStatus

        repo.set_transcription(
            s, eid, text_value="SITREP FOLOWS", confidence=0.4, status=TranscriptionStatus.DONE
        )

    r = client.post(f"/api/events/{eid}/transcription", json={"text": "SITREP FOLLOWS OVER"})
    assert r.status_code == 200
    body = r.json()
    assert body["transcription"] == "SITREP FOLLOWS OVER"
    assert body["transcription_original"] == "SITREP FOLOWS"
    assert body["transcription_edited"] is True

    # The correction is reflected in the log seed the console reads on load.
    r = client.get("/api/events/recent")
    row = next(e for e in r.json() if e["event_id"] == eid)
    assert row["transcription"] == "SITREP FOLLOWS OVER"
    assert row["transcription_edited"] is True


def test_edit_transcription_unknown_event_404(client):
    r = client.post("/api/events/does-not-exist/transcription", json={"text": "x"})
    assert r.status_code == 404


def test_recent_events_survive_restart(client, settings):
    """The console seeds its log from /api/events/recent, which reads the DB —
    recordings and transcripts must survive a server restart or update."""
    manager = client.app.state.manager
    manager.start_session("EX-PERSIST")
    manager.login("ALPHA", "t-1")
    manager.tune("t-1", "14.250 MHz")
    manager.ptt_start("t-1")
    t = np.sin(2 * np.pi * 440 * np.arange(8000) / 16000).astype(np.float32)
    event = manager.ptt_end("t-1", audio=t)
    manager.end_session()

    r = client.get("/api/events/recent")
    assert r.status_code == 200
    assert [e["event_id"] for e in r.json()] == [event["event_id"]]

    # "Restart": a fresh app over the same data dir still lists the event —
    # even though its session has ended — and still streams its clip.
    app2 = create_app(settings)
    app2.dependency_overrides[require_instructor] = lambda: None
    with TestClient(app2) as client2:
        r = client2.get("/api/events/recent")
        assert r.status_code == 200
        assert [e["event_id"] for e in r.json()] == [event["event_id"]]

        r = client2.get(f"/api/events/{event['event_id']}/audio?mode=clean")
        assert r.status_code == 200 and r.content[:4] == b"RIFF"


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

    # /refresh forces a synchronous re-check; the background service caches it so
    # the (non-blocking) /check then returns the same result.
    # Stable channel: prerelease excluded, only 1.1.0 is an available update.
    r = client.post("/api/admin/updates/refresh").json()
    assert r["reachable"] is True and r["current_version"] == version_info.version
    assert [a["tag"] for a in r["available"]] == ["1.1.0"]
    cached = client.get("/api/admin/updates/check").json()
    assert [a["tag"] for a in cached["available"]] == ["1.1.0"]

    # Include prereleases: the rc shows up too (newest first).
    with client.app.state.manager.db.session() as s:
        ConfigStore(s).set("update_channel", "include_prereleases")
    r = client.post("/api/admin/updates/refresh").json()
    assert [a["tag"] for a in r["available"]] == ["1.2.0-rc.1", "1.1.0"]


def test_update_check_graceful_when_unreachable(client, monkeypatch):
    def boom(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr("pivot.updates.github.fetch_releases", boom)
    r = client.post("/api/admin/updates/refresh").json()
    assert r["reachable"] is False and r["available"] == []


def test_default_frequency_setting_snaps_to_channel_raster(client):
    """An off-raster default start frequency is snapped to a tunable channel
    when saved, so operators can't persist a value the radios can't use."""
    r = client.post("/api/admin/settings",
                     json={"default_frequency_hz": 7_003_000.0}).json()
    # 7.003 MHz -> nearest 12.5 kHz channel = 7.0 MHz.
    assert r["applied"]["default_frequency_hz"] == 7_000_000.0

    cfg = client.get("/api/admin/config").json()
    assert cfg["default_frequency_hz"] == 7_000_000.0


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


def test_rx_noise_toggle_over_rest_and_ws(client):
    """The per-radio receive-noise toggle (§3.1.5) flips over REST and over the
    instructor socket, and the instructor_radios push carries the state."""
    token = client.app.state.auth.issue_token()
    radio = client.post("/api/admin/instructor-radios", json={"frequency": "40.000 MHz"}).json()
    rid = radio["radio_id"]
    assert radio["rx_noise"] is True

    r = client.post(f"/api/admin/instructor-radios/{rid}/rx-noise", json={"enabled": False})
    assert r.status_code == 200 and r.json()["rx_noise"] is False
    assert client.get("/api/admin/instructor-radios").json()[0]["rx_noise"] is False
    # Unknown radios 404 (and trainee radios are never instructor radios).
    assert client.post("/api/admin/instructor-radios/instr-999/rx-noise",
                       json={"enabled": True}).status_code == 404

    with client.websocket_connect(f"/ws?token={token}") as wsconn:
        snapshot = _recv_until(wsconn, "instructor_radios")
        assert snapshot["payload"][0]["rx_noise"] is False
        wsconn.send_json({"type": "instr_rx_noise",
                          "payload": {"radio_id": rid, "enabled": True}})
        update = _recv_until(wsconn, "instructor_radios")
        assert update["payload"][0]["rx_noise"] is True


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


def test_instructor_audio_frames_are_tagged_with_radio_id(client):
    # An instructor's radios share one socket, so each rendered frame is prefixed
    # with its source radio_id ([1-byte len][id][PCM…]) — the browser uses the
    # tag to apply that radio's headset volume to the mixed playback stream.
    from pivot.audio.pcm import float32_to_pcm16

    token = client.app.state.auth.issue_token()
    radio = client.post("/api/admin/instructor-radios", json={"frequency": "40.000 MHz"}).json()
    rid = radio["radio_id"]
    client.post("/api/admin/session/start", json={"name": "TAG"})

    with client.websocket_connect(f"/ws?token={token}") as instr:
        for _ in range(4):  # welcome, band profile, instructor_radios, terminal_update
            instr.receive_json()
        # A trainee on the instructor radio's net transmits; the instructor (a
        # listener on that net) receives the rendered, tagged frame.
        with client.websocket_connect("/ws?name=TX&trainee_id=tag-tx") as tx:
            tx.receive_json()  # welcome
            tx.receive_json()  # band profile
            tx.send_json({"type": "tune", "payload": {"frequency": "40.000 MHz"}})
            _recv_until(tx, "tuned")
            tx.send_json({"type": "ptt_start",
                          "payload": {"frequency": "40.000 MHz", "tx_mode": "Plain"}})
            _recv_until(tx, "ptt_started")
            tx.send_bytes(float32_to_pcm16(
                (0.2 * np.sin(2 * np.pi * 440 * np.arange(1600) / 16000)).astype(np.float32)
            ))

            data = _recv_bytes(instr)
            length = data[0]
            assert data[1:1 + length].decode("ascii") == rid
            assert (len(data) - 1 - length) % 2 == 0  # remaining bytes are PCM16
            tx.send_json({"type": "ptt_end", "payload": {}})


def test_instructor_keys_multiple_radios_at_once(client):
    # The instructor can hold several radios keyed and speak once: each binary
    # mic frame is routed to every keyed radio, so each net carries the same
    # source transmission (rendered under its own channel conditions) and each
    # radio logs its own event with its own recording.
    from pivot.audio.pcm import float32_to_pcm16

    token = client.app.state.auth.issue_token()
    r1 = client.post("/api/admin/instructor-radios", json={"frequency": "40.000 MHz"}).json()
    r2 = client.post("/api/admin/instructor-radios", json={"frequency": "7.000 MHz"}).json()
    client.post("/api/admin/session/start", json={"name": "MULTI-KEY"})
    manager = client.app.state.manager
    # A listener on each net so both transmissions classify as 'Heard'.
    manager.login("L-VHF", "listener-vhf")
    manager.tune("listener-vhf", "40.000 MHz")
    manager.login("L-HF", "listener-hf")
    manager.tune("listener-hf", "7.000 MHz")

    with client.websocket_connect(f"/ws?token={token}") as instr:
        for _ in range(4):  # welcome, band profile, instructor_radios, terminal_update
            instr.receive_json()

        for rid in (r1["radio_id"], r2["radio_id"]):
            instr.send_json({"type": "instr_ptt_start",
                             "payload": {"radio_id": rid, "tx_mode": "Plain"}})
            started = _recv_until(instr, "ptt_started")
            assert started["payload"]["radio_id"] == rid

        # One mic frame while both radios are keyed.
        instr.send_bytes(float32_to_pcm16(
            (0.2 * np.sin(2 * np.pi * 440 * np.arange(1600) / 16000)).astype(np.float32)
        ))

        for rid in (r1["radio_id"], r2["radio_id"]):
            instr.send_json({"type": "instr_ptt_end", "payload": {"radio_id": rid}})
            ended = _recv_until(instr, "ptt_ended")
            assert ended["payload"]["radio_id"] == rid
            assert ended["payload"]["audibility"] == "Heard"
            assert ended["payload"]["duration_ms"] > 0  # the frame reached both


def test_radio_added_over_rest_is_heard_on_live_instructor_socket(client):
    # The console adds and removes radios over REST while the instructor WS is
    # already connected. The new radio must start receiving on the live socket
    # (no reconnect), and removing the original radio must not silence it.
    from pivot.audio.pcm import float32_to_pcm16

    token = client.app.state.auth.issue_token()
    first = client.post("/api/admin/instructor-radios", json={"frequency": "40.000 MHz"}).json()
    client.post("/api/admin/session/start", json={"name": "REST-ADD"})

    with client.websocket_connect(f"/ws?token={token}") as instr:
        for _ in range(4):  # welcome, band profile, instructor_radios, terminal_update
            instr.receive_json()

        second = client.post("/api/admin/instructor-radios", json={"frequency": "41.000 MHz"}).json()
        client.delete(f"/api/admin/instructor-radios/{first['radio_id']}")

        with client.websocket_connect("/ws?name=TX&trainee_id=rest-tx") as tx:
            tx.receive_json()  # welcome
            tx.receive_json()  # band profile
            tx.send_json({"type": "ptt_start",
                          "payload": {"frequency": "41.000 MHz", "tx_mode": "Plain"}})
            _recv_until(tx, "ptt_started")
            tx.send_bytes(float32_to_pcm16(
                (0.2 * np.sin(2 * np.pi * 440 * np.arange(1600) / 16000)).astype(np.float32)
            ))

            data = _recv_bytes(instr)
            length = data[0]
            assert data[1:1 + length].decode("ascii") == second["radio_id"]
            tx.send_json({"type": "ptt_end", "payload": {}})


def _recv_until(wsconn, mtype, limit=20):
    for _ in range(limit):
        msg = wsconn.receive_json()
        if msg["type"] == mtype:
            return msg
    raise AssertionError(f"did not receive {mtype!r} within {limit} messages")


def _recv_bytes(wsconn, limit=50):
    for _ in range(limit):
        msg = wsconn.receive()
        if msg.get("bytes") is not None:
            return msg["bytes"]
    raise AssertionError(f"did not receive a binary frame within {limit} messages")

@pytest.mark.asyncio
async def test_schedule_on_air():
    from unittest.mock import AsyncMock, MagicMock, patch

    from pivot.api.ws import _schedule_on_air
    ws_mock = AsyncMock()
    manager_mock = MagicMock()
    with patch("pivot.api.ws.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
        await _schedule_on_air(ws_mock, manager_mock, "test-radio", 1500)
        sleep_mock.assert_called_once_with(1.5)
        manager_mock.ptt_sync_complete.assert_called_once_with("test-radio")
        ws_mock.send_json.assert_called_once_with({"type": "secure_tx", "payload": {"radio_id": "test-radio"}})


@patch("importlib.util.find_spec", return_value=MagicMock())
@patch("pivot.transcription.worker.TranscriptionWorker")
def test_maybe_start_transcription_success(MockWorker, mock_find_spec):
    manager = MagicMock()
    cfg = MagicMock()
    worker = _maybe_start_transcription(manager, cfg)
    MockWorker.assert_called_once_with(manager.db, cfg)
    worker.start.assert_called_once()
    assert worker.on_complete == manager.notify_transcription
    assert manager.transcription_worker == worker
    assert worker == MockWorker.return_value


@patch.dict("sys.modules", {"faster_whisper": None})
def test_maybe_start_transcription_no_whisper():
    manager = MagicMock()
    cfg = MagicMock()
    worker = _maybe_start_transcription(manager, cfg)
    assert worker is None

def test_auth_refresh_mocked(settings):
    from unittest.mock import MagicMock
    from fastapi.testclient import TestClient
    from pivot.api.app import create_app
    from pivot.api.deps import get_auth, require_instructor

    app = create_app(settings)

    mock_auth = MagicMock()
    mock_auth.issue_token.return_value = "new_mocked_token"
    mock_auth.is_default.return_value = True

    app.dependency_overrides[require_instructor] = lambda: None
    app.dependency_overrides[get_auth] = lambda: mock_auth

    with TestClient(app) as c:
        response = c.post("/api/auth/refresh")

        assert response.status_code == 200
        assert response.json() == {"token": None, "must_change_password": True}
        assert c.cookies.get("pivot_token") == "new_mocked_token"
        mock_auth.issue_token.assert_called_once()
        mock_auth.is_default.assert_called_once()


def test_session_events_mocked(client, monkeypatch):
    from unittest.mock import MagicMock
    from pivot.api.deps import get_manager

    mock_manager = MagicMock()
    mock_db_session = MagicMock()
    mock_manager.db.session.return_value.__enter__.return_value = mock_db_session

    mock_event = MagicMock()
    mock_event.to_dict.return_value = {"event_id": "test_event"}

    def mock_list_events(s, session_id):
        return [mock_event]

    monkeypatch.setattr("pivot.api.rest.repo.list_events", mock_list_events)
    monkeypatch.setitem(client.app.dependency_overrides, get_manager, lambda: mock_manager)

    r = client.get("/api/sessions/test-session/events")

    assert r.status_code == 200
    assert r.json() == [{"event_id": "test_event"}]

def test_logout_clears_cookie_and_revokes_token(client, monkeypatch):
    from unittest.mock import MagicMock
    from pivot.api.deps import get_auth

    mock_auth = MagicMock()
    monkeypatch.setitem(client.app.dependency_overrides, get_auth, lambda: mock_auth)

    client.cookies.clear()
    client.cookies.set("pivot_token", "test_cookie_token")

    response = client.post("/api/logout")

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    mock_auth.revoke.assert_called_once_with("test_cookie_token")

    set_cookie = response.headers.get("set-cookie")
    assert set_cookie is not None
    assert 'pivot_token=""' in set_cookie
    assert "Max-Age=0" in set_cookie
    assert "HttpOnly" in set_cookie


def test_logout_with_authorization_header(client, monkeypatch):
    from unittest.mock import MagicMock
    from pivot.api.deps import get_auth

    mock_auth = MagicMock()
    monkeypatch.setitem(client.app.dependency_overrides, get_auth, lambda: mock_auth)

    client.cookies.clear()

    response = client.post("/api/logout", headers={"Authorization": "Bearer test_header_token"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    mock_auth.revoke.assert_called_once_with("test_header_token")


def test_logout_no_token(client, monkeypatch):
    from unittest.mock import MagicMock
    from pivot.api.deps import get_auth

    mock_auth = MagicMock()
    monkeypatch.setitem(client.app.dependency_overrides, get_auth, lambda: mock_auth)

    client.cookies.clear()

    response = client.post("/api/logout")

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    mock_auth.revoke.assert_called_once_with(None)
def test_export_session(client, monkeypatch):
    from pivot.api.deps import get_manager

    mock_manager = MagicMock()
    monkeypatch.setitem(client.app.dependency_overrides, get_manager, lambda: mock_manager)

    def mock_export_text(db, session_id):
        return b"text_content"

    def mock_export_csv(db, session_id):
        return b"csv_content"

    def mock_export_zip(db, settings, session_id):
        return b"zip_content"

    monkeypatch.setattr("pivot.api.rest.exporting.export_text", mock_export_text)
    monkeypatch.setattr("pivot.api.rest.exporting.export_csv", mock_export_csv)
    monkeypatch.setattr("pivot.api.rest.exporting.export_zip", mock_export_zip)

    r = client.post("/api/sessions/session123/export?fmt=text")
    assert r.status_code == 200
    assert r.content == b"text_content"
    assert r.headers["content-type"] == "text/plain; charset=utf-8"
    assert "session123.txt" in r.headers["content-disposition"]

    r = client.post("/api/sessions/session123/export?fmt=csv")
    assert r.status_code == 200
    assert r.content == b"csv_content"
    assert r.headers["content-type"] == "text/csv; charset=utf-8"
    assert "session123.csv" in r.headers["content-disposition"]

    r = client.post("/api/sessions/session123/export?fmt=zip")
    assert r.status_code == 200
    assert r.content == b"zip_content"
    assert r.headers["content-type"] == "application/zip"
    assert "session123.zip" in r.headers["content-disposition"]

    r = client.post("/api/sessions/session123/export")  # Default is zip
    assert r.status_code == 200
    assert r.content == b"zip_content"

    r = client.post("/api/sessions/session123/export?fmt=invalid")
    assert r.status_code == 422


def test_export_session_auth(raw_client):
    r = raw_client.post("/api/sessions/session123/export")
    assert r.status_code == 401
def test_instructor_logout(raw_client):
    from pivot.auth import DEFAULT_INSTRUCTOR_PASSWORD

    # 1. Login
    r = raw_client.post("/api/login", json={"role": "instructor", "password": DEFAULT_INSTRUCTOR_PASSWORD})
    assert r.status_code == 200
    token = raw_client.cookies.get("pivot_token")
    assert token is not None

    headers = {"Authorization": f"Bearer {token}"}

    # Verify we have access
    assert raw_client.get("/api/admin/terminals", headers=headers).status_code == 200

    # 2. Logout
    r = raw_client.post("/api/logout", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    # 3. Verify token cookie is cleared from the client session
    assert not raw_client.cookies.get("pivot_token")

    # 4. Verify the revoked token can no longer access admin endpoints
    assert raw_client.get("/api/admin/terminals", headers=headers).status_code == 401

    # 5. Verify the token can't be refreshed either
    assert raw_client.post("/api/auth/refresh", headers=headers).status_code == 401
def test_admin_start_session_mocked(client, monkeypatch):
    from pivot.api.deps import get_manager
    mock_manager = MagicMock()
    mock_manager.start_session.return_value = {"session_id": 123, "name": "MockSession"}

    monkeypatch.setitem(client.app.dependency_overrides, get_manager, lambda: mock_manager)

    response = client.post("/api/admin/session/start", json={"name": "MockSession"})

    assert response.status_code == 200
    assert response.json() == {"session_id": 123, "name": "MockSession"}
    mock_manager.start_session.assert_called_once_with("MockSession")


def test_recordings_location_returns_absolute_path(client, settings):
    """The location endpoint reports the recordings dir and creates it."""
    resp = client.get("/api/admin/recordings/location")
    assert resp.status_code == 200
    body = resp.json()
    expected = settings.recordings_dir.resolve()
    assert body["path"] == str(expected)
    assert body["exists"] is True
    assert expected.is_dir()  # endpoint ensures the folder exists


def test_recordings_open_success(client, settings):
    """A successful launch reports opened=True with the resolved path."""
    with patch("pivot.runtime.reveal.open_in_file_manager") as opener:
        resp = client.post("/api/admin/recordings/open")
    assert resp.status_code == 200
    body = resp.json()
    assert body["opened"] is True
    assert body["path"] == str(settings.recordings_dir.resolve())
    opener.assert_called_once()


def test_recordings_open_headless_falls_back(client, settings):
    """When the host can't open a file manager, the path is still returned."""
    with (
        patch(
            "pivot.runtime.reveal.open_in_file_manager",
            side_effect=RuntimeError("no display"),
        ),
        patch("pivot.api.rest.log.warning") as log_warning,
    ):
        resp = client.post("/api/admin/recordings/open")
    assert resp.status_code == 200
    body = resp.json()
    assert body["opened"] is False
    assert body["path"] == str(settings.recordings_dir.resolve())
    assert "no display" in body["detail"]
    log_warning.assert_called_once()
    assert "could not open recordings folder" in log_warning.call_args[0][0]


def test_recordings_endpoints_require_instructor(raw_client):
    """Both recordings endpoints are gated behind the instructor token."""
    assert raw_client.get("/api/admin/recordings/location").status_code == 401
    assert raw_client.post("/api/admin/recordings/open").status_code == 401

def test_recordings_open_error_path(client, settings):
    """The fallback logic correctly returns the detail when an error occurs."""
    with patch(
        "pivot.runtime.reveal.open_in_file_manager",
        side_effect=Exception("launch failure"),
    ):
        resp = client.post("/api/admin/recordings/open")
    assert resp.status_code == 200
    body = resp.json()
    assert body["opened"] is False
    assert body["path"] == str(settings.recordings_dir.resolve())
    assert "launch failure" in body["detail"]
