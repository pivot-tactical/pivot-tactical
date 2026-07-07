"""Session export: plain text, CSV, and full ZIP (spec §3.6.4).

* **Text** — a timestamped transcript, one line per event.
* **CSV** — every event field.
* **ZIP** — the text + CSV logs plus all WAV recordings for the session.

Timestamps are presented in the configured display timezone (§3.8) while the
stored values remain UTC.
"""

import csv
import io
import zipfile
from pathlib import Path

from pivot.core.timebase import format_clock, parse_iso_utc
from pivot.db.config_store import ConfigStore
from pivot.db.database import Database
from pivot.db.repository import list_events, list_sessions

_CSV_FIELDS = [
    "event_id",
    "session_id",
    "trainee_name",
    "frequency",
    "band_region",
    "tx_mode",
    "audibility",
    "sync_status",
    "timestamp_start",
    "duration_ms",
    "audio_path",
    "transcription",
    "transcription_confidence",
    "transcription_status",
]


def _events_and_tz(db: Database, session_id: str):
    with db.session() as s:
        tz = ConfigStore(s).display_timezone()
        session = next((x for x in list_sessions(s) if x.id == session_id), None)
        events = [e.to_dict() for e in list_events(s, session_id)]
        name = session.name if session else session_id
    return name, events, tz


def export_text(
    db: Database,
    session_id: str,
    name: str | None = None,
    events: list | None = None,
    tz: str | None = None,
) -> str:
    if events is None or name is None or tz is None:
        name, events, tz = _events_and_tz(db, session_id)

    lines = [f"PIVOT session transcript — {name}", f"Display timezone: {tz}", ""]
    for e in events:
        clock = format_clock(parse_iso_utc(e["timestamp_start"]), tz)
        mode = "CYPHER" if e["tx_mode"] == "Cypher" else "PLAIN"
        text = e["transcription"] or "(no transcription)"
        lines.append(
            f"[{clock}] {e['trainee_name']} {e['frequency']} ({e['band_region']}, {mode}, "
            f"{e['audibility']}): {text}"
        )
    return "\n".join(lines) + "\n"


def export_csv(db: Database, session_id: str, events: list | None = None) -> str:
    if events is None:
        _, events, _ = _events_and_tz(db, session_id)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for e in events:
        writer.writerow(e)
    return buf.getvalue()


def export_zip(db: Database, settings, session_id: str) -> bytes:
    """Full session ZIP: logs + every WAV recording (§3.6.4, acceptance #21)."""
    name, events, tz = _events_and_tz(db, session_id)
    text = export_text(db, session_id, name=name, events=events, tz=tz)
    csv_data = export_csv(db, session_id, events=events)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{session_id}/transcript.txt", text)
        zf.writestr(f"{session_id}/events.csv", csv_data)
        base_dir = Path(settings.recordings_dir).resolve()
        for e in events:
            wav_path = (Path(settings.recordings_dir) / e["audio_path"]).resolve()
            if wav_path.is_relative_to(base_dir) and wav_path.exists():
                zf.write(wav_path, arcname=f"{session_id}/recordings/{Path(e['audio_path']).name}")
    return buf.getvalue()
