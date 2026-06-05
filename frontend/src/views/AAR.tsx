import { useEffect, useState } from "react";
import { api } from "../api";
import type { EventRow, SessionSummary } from "../types";

// After Action Review (spec §3.6, §7.2.3): session list + event timeline with
// the global Clean/Dirty and Plain/Cypher playback toggles and export.
const CONF_THRESHOLD = 0.8; // amber below this (§3.1.6 default)

export function AAR({ onBack }: { onBack: () => void }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [dirty, setDirty] = useState(false); // Clean (false) / Dirty (true)
  const [cypherView, setCypherView] = useState(false); // Plain (false) / Cypher (true)
  const [audio] = useState(() => new Audio());

  useEffect(() => {
    api.sessions().then(setSessions).catch(() => setSessions([]));
  }, []);

  useEffect(() => {
    if (selected) api.events(selected).then(setEvents).catch(() => setEvents([]));
    else setEvents([]);
  }, [selected]);

  function play(ev: EventRow) {
    audio.pause();
    audio.src = api.eventAudioUrl(
      ev.event_id,
      dirty ? "dirty" : "clean",
      cypherView ? "cypher" : "plain"
    );
    audio.play().catch(() => {});
  }

  return (
    <div className="aar">
      <header className="aar__bar">
        <button className="btn btn--ghost" onClick={onBack}>← Radio</button>
        <h2>After Action Review</h2>
        <div className="aar__toggles">
          <Toggle label={dirty ? "DIRTY" : "CLEAN"} active={dirty} onClick={() => setDirty((v) => !v)} />
          <Toggle
            label={cypherView ? "CYPHER VIEW" : "PLAIN VIEW"}
            active={cypherView}
            onClick={() => setCypherView((v) => !v)}
          />
          {selected && (
            <a className="btn btn--ghost" href={api.exportUrl(selected, "zip")}>Export ZIP</a>
          )}
        </div>
      </header>

      <div className="aar__body">
        <aside className="aar__sessions">
          {sessions.length === 0 && <p className="muted">No sessions recorded yet.</p>}
          {sessions.map((s) => (
            <button
              key={s.id}
              className={`session ${selected === s.id ? "session--active" : ""}`}
              onClick={() => setSelected(s.id)}
            >
              <div className="session__name">{s.name}</div>
              <div className="session__meta mono">
                {s.started_at.slice(0, 19).replace("T", " ")} · {s.event_count ?? 0} events
              </div>
            </button>
          ))}
        </aside>

        <main className="aar__timeline">
          {!selected && <p className="muted">Select a session to view its timeline.</p>}
          {selected && events.length === 0 && <p className="muted">No events in this session.</p>}
          {events.map((ev) => (
            <EventRowView key={ev.event_id} ev={ev} onPlay={() => play(ev)} />
          ))}
        </main>
      </div>
    </div>
  );
}

function EventRowView({ ev, onPlay }: { ev: EventRow; onPlay: () => void }) {
  const time = ev.timestamp_start.slice(11, 19);
  const low = ev.transcription_confidence != null && ev.transcription_confidence < CONF_THRESHOLD;
  const noText = !ev.transcription;
  return (
    <div className="event">
      <button className="event__play" onClick={onPlay} title="Play">▶</button>
      <span className="event__time mono">{time}</span>
      <span className="event__name mono">{ev.trainee_name}</span>
      <span className="event__freq mono">{ev.frequency}<small> {ev.band_region}</small></span>
      <span className="event__mode" title={ev.tx_mode}>{ev.tx_mode === "Cypher" ? "🔒" : "◌"}</span>
      <span className={`event__aud aud--${ev.audibility.split("-")[0].toLowerCase()}`}>{ev.audibility}</span>
      <span className={`event__text ${low ? "text--amber" : ""} ${noText ? "text--none" : ""}`}>
        {ev.transcription || (ev.transcription_status === "Pending" ? "transcribing…" : "—")}
      </span>
      <span className="event__dur mono">{(ev.duration_ms / 1000).toFixed(1)}s</span>
    </div>
  );
}

function Toggle({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button className={`toggle toggle--sm ${active ? "toggle--on" : ""}`} onClick={onClick}>
      {label}
    </button>
  );
}
