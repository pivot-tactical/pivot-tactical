import { useEffect, useState } from "react";
import { api } from "../../api";
import type { EventRow, SessionSummary } from "../../types";
import { fmtDateTime } from "./utils";

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  let v = n / 1024;
  const units = ["KB", "MB", "GB"];
  let u = 0;
  while (v >= 1024 && u < units.length - 1) { v /= 1024; u++; }
  return `${v.toFixed(1)} ${units[u]}`;
}

export function AarTab({ timezone }: { timezone: string }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [loadingEvents, setLoadingEvents] = useState(false);
  const [busy, setBusy] = useState<"zip" | "text" | "csv" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.sessions().then((rows) => {
      const sorted = [...rows].sort((a, b) => b.started_at.localeCompare(a.started_at));
      setSessions(sorted);
      setSelectedId((cur) => cur ?? sorted[0]?.id ?? null);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!selectedId) { setEvents([]); return; }
    let cancelled = false;
    setLoadingEvents(true);
    api.events(selectedId)
      .then((rows) => { if (!cancelled) setEvents(rows); })
      .catch(() => { if (!cancelled) setEvents([]); })
      .finally(() => { if (!cancelled) setLoadingEvents(false); });
    return () => { cancelled = true; };
  }, [selectedId]);

  const selected = sessions.find((s) => s.id === selectedId) ?? null;

  async function doExport(fmt: "zip" | "text" | "csv") {
    if (!selected) return;
    setBusy(fmt);
    setError(null);
    try {
      await api.exportSession(selected.id, fmt, selected.name);
    } catch {
      setError("Export failed — please try again.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="aar">
      <div className="aar__bar">
        <h2 className="mono">{selected ? selected.name : "After Action Review"}</h2>
        <div className="aar__toggles">
          <button className="btn" disabled={!selected || busy !== null} onClick={() => doExport("text")}>
            {busy === "text" ? "Exporting…" : "Export Text"}
          </button>
          <button className="btn" disabled={!selected || busy !== null} onClick={() => doExport("csv")}>
            {busy === "csv" ? "Exporting…" : "Export CSV"}
          </button>
          <button className="btn btn--primary" disabled={!selected || busy !== null} onClick={() => doExport("zip")}>
            {busy === "zip" ? "Exporting…" : "Export ZIP + audio"}
          </button>
        </div>
      </div>
      {error && <div className="aar__error">{error}</div>}
      <div className="aar__body">
        <div className="aar__sessions">
          {sessions.length === 0 && <p className="muted" style={{ padding: 10 }}>No sessions recorded yet.</p>}
          {sessions.map((s) => (
            <button
              key={s.id}
              className={`session ${s.id === selectedId ? "session--active" : ""}`}
              onClick={() => setSelectedId(s.id)}
            >
              <div className="session__name">{s.name}</div>
              <div className="session__meta">
                {fmtDateTime(s.started_at, timezone)}
                {s.event_count != null && ` · ${s.event_count} tx`}
                {s.ended_at == null && " · live"}
              </div>
            </button>
          ))}
        </div>
        <div className="aar__timeline">
          {loadingEvents && <p className="muted">Loading transmissions…</p>}
          {!loadingEvents && selected && events.length === 0 && (
            <p className="muted">No transmissions in this session.</p>
          )}
          {!loadingEvents && events.map((ev) => <AarRow key={ev.event_id} ev={ev} />)}
        </div>
      </div>
    </section>
  );
}

export function AarRow({ ev }: { ev: EventRow }) {
  const low = !ev.transcription_edited &&
    ev.transcription_confidence != null && ev.transcription_confidence < 0.8;
  const text = ev.transcription?.trim();
  const durS = (ev.duration_ms / 1000).toFixed(1);
  return (
    <div className="event event--readonly">
      <span className="event__time mono">{ev.timestamp_start.slice(11, 19)}</span>
      <span className="mono">{ev.trainee_name}</span>
      <span className="event__freq mono">{ev.frequency} <small>{ev.band_region}</small></span>
      <span className="event__mode" title={ev.tx_mode}>{ev.tx_mode === "Cypher" ? "🔒" : "◌"}</span>
      <span className={`event__aud aud--${ev.audibility.split("-")[0].toLowerCase()}`}>{ev.audibility}</span>
      <span className={`event__text ${low ? "text--amber" : ""} ${text ? "" : "text--none"}`}>
        {ev.jammed && <span className="event__jammed" title="Channel was jammed">JAMMED</span>}
        {text || "(no transcription)"}
        {ev.transcription_edited && <span className="muted"> [edited]</span>}
      </span>
      <span className="event__dur mono">{durS}s</span>
    </div>
  );
}
