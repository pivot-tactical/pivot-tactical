import React, { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import type { EventRow, LogEntry } from "../../types";
import { fmtLogStamp } from "./utils";

export function LiveLogTab({ entries, timezone, onEventUpdate }: {
  entries: LogEntry[];
  timezone: string;
  onEventUpdate: (ev: Partial<EventRow> & { event_id: string }) => void;
}) {
  const [audio] = useState(() => new Audio());

  function play(ev: EventRow, mode: "clean" | "dirty") {
    audio.src = api.eventAudioUrl(ev.event_id, mode);
    audio.play().catch(() => {});
  }

  return (
    <section className="card liveness-log">
      <h2>Running Event Log</h2>
      <div className="logtable">
        <div className="loghead">
          <span>AUDIO</span><span>TIMESTAMP</span><span>TRAINEE</span><span>FREQ</span><span>MODE</span><span>AUDIBILITY</span><span>TRANSCRIPTION</span>
        </div>
        {entries.map((entry) => {
          if (entry.kind === "session") {
            const m = entry.marker;
            const stamp = fmtLogStamp(m.timestamp, timezone);
            return (
              <div className="logrow logrow--session" key={`sess-${m.session_id}-${m.type}`}>
                {/* A divider row, not a grid column — room for the full stamp. */}
                <span className="mono muted">{`${stamp.date} ${stamp.time}`}</span>
                <span className="logrow__session-label">
                  Session “{m.session_name}” {m.type === "started" ? "started" : "stopped"}
                </span>
              </div>
            );
          }
          const ev = entry.event;
          const stamp = fmtLogStamp(ev.timestamp_start, timezone);
          return (
            <div className="logrow" key={ev.event_id}>
              <span className="event__play-group">
                <button className="event__play" onClick={() => play(ev, "clean")} aria-label="Play without noise" title="Play without noise">▶</button>
                <button className="event__play" onClick={() => play(ev, "dirty")} aria-label="Play with noise (as heard)" title="Play with noise (as heard)">📻</button>
              </span>
              <span className="mono muted logstamp">
                <span className="logstamp__date">{stamp.date}</span>
                <span>{stamp.time}</span>
              </span>
              <span className="mono">{ev.trainee_name}</span>
              <span className="mono">{ev.frequency}</span>
              <span title={ev.tx_mode}>{ev.tx_mode === "Cypher" ? "🔒" : "◌"}</span>
              <span className={`event__aud aud--${ev.audibility.split("-")[0].toLowerCase()}`}>{ev.audibility}</span>
              <TranscriptCell ev={ev} onEventUpdate={onEventUpdate} />
            </div>
          );
        })}
      </div>
    </section>
  );
}

function TranscriptCell({ ev, onEventUpdate }: {
  ev: EventRow;
  onEventUpdate: (ev: Partial<EventRow> & { event_id: string }) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const taRef = useRef<HTMLTextAreaElement>(null);

  const pending = ev.transcription_status === "Pending";
  const low = !ev.transcription_edited &&
    ev.transcription_confidence != null && ev.transcription_confidence < 0.8;

  function begin() {
    if (pending) return;
    setDraft(ev.transcription ?? "");
    setEditing(true);
  }
  useEffect(() => {
    if (editing) {
      const ta = taRef.current;
      ta?.focus();
      ta?.select();
    }
  }, [editing]);

  async function confirm() {
    const text = draft.trim();
    if (text === (ev.transcription ?? "").trim()) { setEditing(false); return; }
    setSaving(true);
    try {
      const updated = await api.editTranscription(ev.event_id, text);
      onEventUpdate(updated);
      setEditing(false);
    } catch {
      // Leave the box open so the instructor can retry without losing the text.
    } finally {
      setSaving(false);
    }
  }

  const jammedBadge = ev.jammed && (
    <span
      className="event__jammed"
      title={`Captured while its channel was jammed${
        ev.snr_db != null ? ` (SNR ${Math.round(ev.snr_db)} dB)` : ""
      }. "Play with noise" re-renders it as a wall of jammer noise.`}
    >
      JAMMED
    </span>
  );

  if (editing) {
    return (
      <span className="logtext transcript transcript--editing">
        {jammedBadge}
        <textarea
          ref={taRef}
          className="input transcript__box"
          rows={2}
          value={draft}
          disabled={saving}
          aria-label="Edit transcript"
          placeholder="Type what was said…"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); confirm(); }
            else if (e.key === "Escape") { e.preventDefault(); setEditing(false); }
          }}
        />
        <span className="transcript__actions">
          <button className="btn btn--primary btn--tiny" onClick={confirm} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button className="btn btn--tiny" onClick={() => setEditing(false)} disabled={saving}>
            Cancel
          </button>
        </span>
      </span>
    );
  }

  const hasText = !!ev.transcription;
  const body = (
    <>
      {jammedBadge}
      {ev.transcription_edited && hasText
        ? renderDiff(ev.transcription_original, ev.transcription!)
        : (ev.transcription || (pending ? "transcribing…" : "—"))}
      {ev.transcription_edited && (
        <span className="transcript__badge" title="Manually edited — highlighted words differ from the machine transcription.">✎ edited</span>
      )}
    </>
  );

  if (pending) {
    return <span className="logtext transcript text--none">{body}</span>;
  }
  return (
    <span
      className={`logtext transcript ${low ? "text--amber" : ""} ${!hasText ? "text--none" : ""}`}
      role="button"
      tabIndex={0}
      title="Click to edit the transcript"
      onClick={begin}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); begin(); } }}
    >
      {body}
    </span>
  );
}

export function lcsOps<T extends string>(a: T[], b: T[]): { t: "eq" | "del" | "ins"; v: T }[] {
  const n = a.length, m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: { t: "eq" | "del" | "ins"; v: T }[] = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push({ t: "eq", v: b[j] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ t: "del", v: a[i] }); i++; }
    else { out.push({ t: "ins", v: b[j] }); j++; }
  }
  while (i < n) { out.push({ t: "del", v: a[i] }); i++; }
  while (j < m) { out.push({ t: "ins", v: b[j] }); j++; }
  return out;
}

export function renderWord(from: string | null, to: string, key: React.Key): React.ReactNode {
  const whole = <mark key={key} className="transcript__edit">{to}</mark>;
  if (from == null) return whole;
  const chars = lcsOps([...from], [...to]).filter((o) => o.t !== "del");
  const unchanged = chars.filter((c) => c.t === "eq").length;
  const isTypoFix = unchanged / Math.max(from.length, to.length) >= 0.5;
  if (!isTypoFix) return whole;
  const nodes: React.ReactNode[] = [];
  let buf = "", changed = chars.length > 0 && chars[0].t === "ins", part = 0;
  const flush = () => {
    if (!buf) return;
    nodes.push(
      changed
        ? <mark key={`${key}-${part}`} className="transcript__edit">{buf}</mark>
        : <span key={`${key}-${part}`}>{buf}</span>
    );
    buf = ""; part++;
  };
  for (const c of chars) {
    const isChanged = c.t === "ins";
    if (isChanged !== changed) { flush(); changed = isChanged; }
    buf += c.v;
  }
  flush();
  return <span key={key}>{nodes}</span>;
}

export function renderDiff(original: string | null, edited: string): React.ReactNode {
  const a = original ? original.split(/\s+/).filter(Boolean) : [];
  const b = edited.split(/\s+/).filter(Boolean);
  const ops = lcsOps(a, b);
  const words: React.ReactNode[] = [];
  let k = 0;
  while (k < ops.length) {
    if (ops[k].t === "eq") { words.push(ops[k].v); k++; continue; }
    const dels: string[] = [], inss: string[] = [];
    while (k < ops.length && ops[k].t !== "eq") {
      if (ops[k].t === "del") dels.push(ops[k].v); else inss.push(ops[k].v);
      k++;
    }
    inss.forEach((w, idx) =>
      words.push(renderWord(idx < dels.length ? dels[idx] : null, w, `w${words.length}`))
    );
  }
  return words.map((n, idx) => <span key={idx}>{idx > 0 ? " " : ""}{n}</span>);
}
