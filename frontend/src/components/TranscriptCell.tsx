import React, { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { EventRow } from "../types";

// Diff of a hand-corrected transcript against the machine one, hybrid word +
// character (§3.5.3). Words are aligned first (a word is the meaningful unit of
// a transcript, and aligning on words keeps a reworded phrase from fragmenting).
// A word the instructor *replaced* is then refined to the character level so a
// single-digit or suffix typo fix highlights only the characters that changed —
// but only when the two words are similar enough; a wholesale reword highlights
// the whole word instead of scattering marks across it. A null original means
// the line was typed from scratch, so all of it is a change.

// Longest-common-subsequence alignment of two token arrays into ordered ops.
function lcsOps<T extends string>(a: T[], b: T[]): { t: "eq" | "del" | "ins"; v: T }[] {
  const n = a.length, m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: { t: "eq" | "del" | "ins"; v: T }[] = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push({ t: "eq", v: b[j] }); i++; j++; }
    else if (dp[i + 1][j] > dp[i][j + 1]) { out.push({ t: "del", v: a[i] }); i++; }
    else { out.push({ t: "ins", v: b[j] }); j++; }
  }
  while (i < n) { out.push({ t: "del", v: a[i] }); i++; }
  while (j < m) { out.push({ t: "ins", v: b[j] }); j++; }
  return out;
}

// Render one word `to` that replaced machine word `from` (or was inserted when
// `from` is null): highlight the characters that differ, or the whole word when
// the two are too dissimilar to refine cleanly.
function renderWord(from: string | null, to: string, key: React.Key): React.ReactNode {
  const whole = <mark key={key} className="transcript__edit">{to}</mark>;
  if (from == null) return whole;
  const chars = lcsOps([...from], [...to]).filter((o) => o.t !== "del");
  const unchanged = chars.filter((c) => c.t === "eq").length;
  // Refine only when at least half the correction's characters are shared with
  // the machine word — otherwise it's a reword, not a typo fix.
  const isTypoFix = unchanged / Math.max(from.length, to.length) >= 0.5;
  if (!isTypoFix) return whole;
  // Coalesce adjacent same-kind characters into as few marks as possible.
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

function renderDiff(original: string | null, edited: string): React.ReactNode {
  const a = original ? original.split(/\s+/).filter(Boolean) : [];
  const b = edited.split(/\s+/).filter(Boolean);
  const ops = lcsOps(a, b);
  const words: React.ReactNode[] = [];
  let k = 0;
  while (k < ops.length) {
    if (ops[k].t === "eq") { words.push(ops[k].v); k++; continue; }
    // A maximal run of deletes/inserts is one replace block; pair each new word
    // with the machine word it replaced (by position) for character refinement.
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

// One transcript, single-click-to-edit (§3.5.3). Displayed it shows the current
// text; a machine transcription below the amber-confidence threshold is tinted,
// and a hand-corrected one diffs against the preserved machine text so the words
// the instructor changed are highlighted. Clicking swaps in a freeform box;
// Enter (or Save) confirms, Escape (or Cancel) reverts.
export function TranscriptCell({ ev, onEventUpdate }: {
  ev: EventRow;
  onEventUpdate: (ev: Partial<EventRow> & { event_id: string }) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const taRef = useRef<HTMLTextAreaElement>(null);

  // Don't offer editing until the machine transcription has settled — editing a
  // row still being transcribed would race the worker's result.
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
    // No-op edit: just close the box.
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
            // Enter confirms (Shift+Enter inserts a newline); Escape cancels.
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
  // The "edited" badge trails the text so a corrected row's transcript still
  // starts at the same left edge as every other row (the badge on the left
  // bumped it out of column alignment).
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

  // While the machine transcription is still pending there's nothing to correct
  // yet, so it's shown as plain (non-editable) text; every settled row is
  // single-click-to-edit.
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
