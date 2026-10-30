import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { errorMessage } from "../errors";
import type { EventRow, SessionSummary } from "../types";
import "./AarReview.css";

type Confidence = "all" | "low" | "high" | "unknown";
type ExportFormat = "zip" | "text" | "csv";

function dateTime(iso: string, timezone: string): string {
  const date = new Date(iso);
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: timezone, year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    }).format(date);
  } catch {
    return iso;
  }
}

function lowConfidence(event: EventRow): boolean {
  return !event.transcription_edited && event.transcription_confidence != null &&
    event.transcription_confidence < 0.8;
}

// One player for the selected transmission prevents overlapping recordings.
// Native controls provide pause, seeking and keyboard playback controls.
function ReviewAudio({ event, mode, view }: {
  event: EventRow; mode: "clean" | "dirty"; view: "plain" | "cypher";
}) {
  const player = useRef<HTMLAudioElement>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const source = useMemo(
    () => api.eventAudioUrl(event.event_id, mode, view),
    [event.event_id, mode, view, attempt],
  );

  useEffect(() => {
    setError(false);
    const element = player.current;
    return () => { element?.pause(); };
  }, [source]);

  return (
    <div className="aar-review__player">
      <audio key={source} ref={player} controls autoPlay preload="metadata" src={source}
        aria-label={`Recording from ${event.trainee_name}`}
        onError={() => setError(true)} />
      {error && (
        <div role="alert" className="aar-review__notice">
          Recording could not be played. Check the server connection and try again.
          <button className="btn" onClick={() => { setError(false); setAttempt((n) => n + 1); }}>Retry recording</button>
        </div>
      )}
    </div>
  );
}

const EXPORT_TITLES: Record<ExportFormat, string> = {
  text: "Export transcript as text",
  csv: "Export transcript as CSV",
  zip: "Export audio and transcript as ZIP",
};

const NO_SESSIONS = "No sessions recorded yet. Start a session in the Radios tab to begin recording.";

export function AarReview({ timezone }: { timezone: string }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [sessionsError, setSessionsError] = useState("");
  const [sessionsAttempt, setSessionsAttempt] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selectedIdRef = useRef(selectedId);
  selectedIdRef.current = selectedId;
  const [eventResult, setEventResult] = useState<{ sessionId: string; rows: EventRow[] } | null>(null);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventsError, setEventsError] = useState("");
  const [eventsAttempt, setEventsAttempt] = useState(0);
  const [exportBusy, setExportBusy] = useState<ExportFormat | null>(null);
  const [exportError, setExportError] = useState("");
  const [callsign, setCallsign] = useState("");
  const [frequency, setFrequency] = useState("");
  const [confidence, setConfidence] = useState<Confidence>("all");
  const [activeEvent, setActiveEvent] = useState<string | null>(null);
  const [mode, setMode] = useState<"clean" | "dirty">("dirty");
  const [view, setView] = useState<"plain" | "cypher">("cypher");

  useEffect(() => {
    let cancelled = false;
    setSessionsLoading(true);
    setSessionsError("");
    api.sessions().then((rows) => {
      if (cancelled) return;
      const sorted = [...rows].sort((a, b) => b.started_at.localeCompare(a.started_at));
      setSessions(sorted);
      setSelectedId((current) => sorted.some((s) => s.id === current) ? current : sorted[0]?.id ?? null);
    }).catch((error: unknown) => { if (!cancelled) setSessionsError(errorMessage(error)); })
      .finally(() => { if (!cancelled) setSessionsLoading(false); });
    return () => { cancelled = true; };
  }, [sessionsAttempt]);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    setEventsLoading(true);
    setEventsError("");
    setActiveEvent(null);
    api.events(selectedId).then((rows) => {
      if (!cancelled) setEventResult({ sessionId: selectedId, rows });
    }).catch((error: unknown) => { if (!cancelled) setEventsError(errorMessage(error)); })
      .finally(() => { if (!cancelled) setEventsLoading(false); });
    return () => { cancelled = true; };
  }, [selectedId, eventsAttempt]);

  useEffect(() => { setActiveEvent(null); }, [callsign, frequency, confidence]);

  const selected = sessions.find((s) => s.id === selectedId) ?? null;
  const events = eventResult?.sessionId === selectedId ? eventResult.rows : [];
  const callsigns = [...new Set(events.map((event) => event.trainee_name))].sort();
  const visible = events.filter((event) => {
    if (callsign && event.trainee_name !== callsign) return false;
    if (frequency.trim() && !event.frequency.toLowerCase().includes(frequency.trim().toLowerCase())) return false;
    if (confidence === "low" && !lowConfidence(event)) return false;
    if (confidence === "high" && !(event.transcription_edited ||
      (event.transcription_confidence != null && event.transcription_confidence >= 0.8))) return false;
    if (confidence === "unknown" && (event.transcription_edited || event.transcription_confidence != null)) return false;
    return true;
  });

  function selectSession(id: string) {
    setSelectedId(id);
    setActiveEvent(null);
    setCallsign("");
    setFrequency("");
    setConfidence("all");
    setExportError("");
  }

  async function exportSession(format: ExportFormat) {
    if (!selected || exportBusy) return;
    setExportBusy(format);
    setExportError("");
    try { await api.exportSession(selected.id, format, selected.name); }
    catch (error: unknown) {
      if (selectedIdRef.current === selected.id) setExportError(errorMessage(error));
    }
    finally { setExportBusy(null); }
  }

  return (
    <section className="aar aar-review">
      <div className="aar__bar">
        <h2 className="mono">{selected?.name ?? "After Action Review"}</h2>
        <div className="aar__toggles">
          {(["text", "csv", "zip"] as ExportFormat[]).map((format) => (
            <button key={format} className={`btn ${format === "zip" ? "btn--primary" : ""}`}
              disabled={!selected || exportBusy !== null} onClick={() => exportSession(format)}
              title={!selected ? "Select a session to export" : EXPORT_TITLES[format]}>
              {exportBusy === format ? "Exporting…" : format === "zip" ? "Export ZIP + audio" : `Export ${format === "text" ? "Text" : "CSV"}`}
            </button>
          ))}
        </div>
      </div>
      {exportError && <div className="aar__error" role="alert">Export failed. {exportError}</div>}
      <div className="aar__body">
        <aside className="aar__sessions" aria-label="Recorded sessions">
          {sessionsLoading && <p role="status" className="muted">Loading sessions…</p>}
          {sessionsError && <div role="alert" className="aar-review__notice">
            Could not load sessions. {sessionsError}
            <button className="btn" onClick={() => setSessionsAttempt((n) => n + 1)}>Retry sessions</button>
          </div>}
          {!sessionsLoading && !sessionsError && sessions.length === 0 && <p className="muted">{NO_SESSIONS}</p>}
          {sessions.map((session) => (
            <button key={session.id} className={`session ${session.id === selectedId ? "session--active" : ""}`}
              aria-pressed={session.id === selectedId} onClick={() => selectSession(session.id)}>
              <div className="session__name">{session.name}</div>
              <div className="session__meta">
                {dateTime(session.started_at, timezone)}
                {session.event_count != null && ` · ${session.event_count} tx`}
                {session.ended_at == null && " · live"}
              </div>
            </button>
          ))}
        </aside>
        <div className="aar__timeline">
          {selected && <>
            <div className="aar-review__filters">
              <label>Callsign<select className="input" aria-label="Filter by callsign" value={callsign}
                onChange={(e) => setCallsign(e.target.value)}>
                <option value="">All callsigns</option>
                {callsigns.map((name) => <option key={name}>{name}</option>)}
              </select></label>
              <label>Frequency<input className="input" aria-label="Filter by frequency" placeholder="e.g. 14.250 MHz"
                value={frequency} onChange={(e) => setFrequency(e.target.value)} /></label>
              <label>Transcript confidence<select className="input" aria-label="Filter by transcript confidence"
                value={confidence} onChange={(e) => setConfidence(e.target.value as Confidence)}>
                <option value="all">All confidence levels</option>
                <option value="low">Low confidence (&lt;80%)</option>
                <option value="high">High confidence / reviewed</option>
                <option value="unknown">Confidence unavailable</option>
              </select></label>
              <button className="btn" onClick={() => { setCallsign(""); setFrequency(""); setConfidence("all"); }}>Clear filters</button>
            </div>
            <div className="aar-review__playback">
              <label>Audio<select className="input" aria-label="Playback audio" value={mode}
                onChange={(e) => setMode(e.target.value as "clean" | "dirty")}>
                <option value="clean">Clean — original microphone</option>
                <option value="dirty">Dirty — radio effects</option>
              </select></label>
              <label>Receiver mode<select className="input" aria-label="Playback receiver mode" value={view} disabled={mode === "clean"}
                onChange={(e) => setView(e.target.value as "plain" | "cypher")}>
                <option value="plain">Plain — cypher heard as hash</option>
                <option value="cypher">Cypher — decoded voice</option>
              </select>{mode === "clean" && <span>Receiver mode applies to Dirty audio. Clean plays the original microphone recording.</span>}</label>
              <span className="muted">Times shown in {timezone}</span>
            </div>
          </>}
          {eventsLoading && <p role="status" className="muted">Loading transmissions…</p>}
          {eventsError && <div role="alert" className="aar-review__notice">
            Could not load transmissions. {eventsError}
            <button className="btn" onClick={() => setEventsAttempt((n) => n + 1)}>Retry transmissions</button>
          </div>}
          {!eventsLoading && !eventsError && selected && <>
            <p className="muted" role="status">{visible.length} of {events.length} transmissions</p>
            {events.length === 0 ? <p className="muted">No transmissions in this session.</p> :
              visible.length === 0 && <p className="muted">No transmissions match these filters.</p>}
            {visible.map((event) => (
              <article className="aar-review__event card" key={event.event_id}>
                <div className="aar-review__event-head">
                  <button className="btn" onClick={() => setActiveEvent(activeEvent === event.event_id ? null : event.event_id)}
                    aria-label={`${activeEvent === event.event_id ? "Close" : "Play"} recording from ${event.trainee_name} at ${dateTime(event.timestamp_start, timezone)}`}>
                    {activeEvent === event.event_id ? "Close recording" : "▶ Play"}
                  </button>
                  <strong className="mono">{event.trainee_name}</strong>
                  <time dateTime={event.timestamp_start}>{dateTime(event.timestamp_start, timezone)}</time>
                  <span className="mono">{event.frequency} · {event.band_region}</span>
                  <span>{event.tx_mode}</span>
                  <span>{event.audibility}</span>
                  <span className="muted">{(event.duration_ms / 1000).toFixed(1)}s</span>
                  {event.jammed && <span className="event__jammed">JAMMED</span>}
                </div>
                <p className={lowConfidence(event) ? "text--amber" : ""}>
                  {event.transcription?.trim() || (event.transcription_status === "Pending" ? "Transcribing…" : "(no transcription)")}
                  {event.transcription_edited && <span className="muted"> [edited]</span>}
                  {lowConfidence(event) && <span className="aar-review__confidence">Low confidence</span>}
                </p>
                {activeEvent === event.event_id && <ReviewAudio key={event.event_id} event={event} mode={mode} view={view} />}
              </article>
            ))}
          </>}
        </div>
      </div>
    </section>
  );
}
