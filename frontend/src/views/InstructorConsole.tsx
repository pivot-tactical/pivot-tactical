import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { ReleaseInfo, UpdateStatus } from "../api";
import { AudioIO, loadVolume, parseTaggedAudio, pcmLevel, playClick, playSyncTone, saveVolume } from "../audio";
import { ConnectionBanner } from "../components/ConnectionBanner";
import type { ConnState } from "../components/ConnectionBanner";
import { ModeDial } from "../components/ModeDial";
import { SevenSegmentClock } from "../components/SevenSegmentClock";
import { METER_DECAY, SignalMeter } from "../components/SignalMeter";
import { VolumeSlider } from "../components/VolumeSlider";
import type { EventRow, LogEntry, NetScenario, RadioState, SessionLogMarker, Terminal, TxPhase } from "../types";
import { PivotSocket } from "../ws";

type Tab = "radios" | "monitor" | "settings";

const FALLBACK_TIMEZONES = [
  "UTC", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
  "America/Anchorage", "Pacific/Honolulu", "Europe/London", "Europe/Berlin", "Europe/Paris",
  "Europe/Moscow", "Africa/Cairo", "Asia/Jerusalem", "Asia/Dubai", "Asia/Karachi",
  "Asia/Kolkata", "Asia/Bangkok", "Asia/Shanghai", "Asia/Tokyo", "Australia/Sydney",
  "Pacific/Auckland",
];

// Sort key for merging history events and session markers into one timeline.
function timestampOf(e: LogEntry): string {
  return e.kind === "event" ? e.event.timestamp_start : e.marker.timestamp;
}

function getTimezoneOptions(): string[] {
  try {
    const supported = (Intl as any).supportedValuesOf?.("timeZone");
    if (Array.isArray(supported) && supported.length) return supported;
  } catch {
    // fall through to fallback list
  }
  return FALLBACK_TIMEZONES;
}

export function InstructorConsole({
  timezone,
  mustChangePassword,
  onTimezone,
  onLogout,
}: {
  timezone: string;
  mustChangePassword: boolean;
  onTimezone: (tz: string) => void;
  onLogout: () => void;
}) {
  const [tab, setTab] = useState<Tab>(mustChangePassword ? "settings" : "radios");
  const [radios, setRadios] = useState<RadioState[]>([]);
  const [terminals, setTerminals] = useState<Terminal[]>([]);
  const [netScenarios, setNetScenarios] = useState<NetScenario[]>([]);
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [sessionActive, setSessionActive] = useState(false);
  const [sessionName, setSessionName] = useState("");
  const [conn, setConn] = useState<ConnState>("online");
  const restartingRef = useRef(false);
  const restartPollRef = useRef<number | undefined>(undefined);
  const socketRef = useRef<PivotSocket | null>(null);
  const audio = useRef(new AudioIO());
  // Live receive level per instructor radio, topped up by each tagged PCM
  // frame and decayed by each card's meter loop. A ref (not state): the meters
  // write straight to the DOM at animation rate.
  const rxLevels = useRef<Record<string, number>>({});

  // Poll the server until it answers again, then reload to pick up the (possibly
  // updated) frontend and a clean session. Started once the socket has dropped.
  function startRestartPoll() {
    if (restartPollRef.current !== undefined) return;
    restartPollRef.current = window.setInterval(() => {
      api.status()
        .then(() => {
          window.clearInterval(restartPollRef.current);
          window.location.reload();
        })
        .catch(() => {});
    }, 1500);
  }

  useEffect(() => {
    const sock = new PivotSocket(() => ({}));
    sock.on("open", () => setConn("online"));
    sock.on("close", () => {
      if (restartingRef.current) { setConn("restarting"); startRestartPoll(); }
      else setConn("offline");
    });
    sock.on("instructor_radios", (p) => setRadios(p));
    // band_profile_update payloads are partial; only the keys present changed.
    // The connect-time snapshot carries the full per-net override list.
    sock.on("band_profile_update", (p) => {
      if (p.net_scenarios) setNetScenarios(p.net_scenarios);
    });
    sock.on("terminal_update", (p) => setTerminals((p.terminals || []).filter((t: Terminal) => !t.is_instructor)));
    sock.on("event_logged", (ev) =>
      setEntries((prev) => [{ kind: "event", event: ev } as LogEntry, ...prev].slice(0, 200))
    );
    sock.on("transcription_updated", (ev) =>
      setEntries((prev) =>
        prev.map((e) =>
          e.kind === "event" && e.event.event_id === ev.event_id
            ? { kind: "event", event: { ...e.event, ...ev } }
            : e
        )
      )
    );
    // Session start/stop get their own divider rows in the log, timestamped so
    // the boundary between exercises is visible after the fact.
    sock.on("session_started", (p) => {
      setSessionActive(true);
      setEntries((prev) =>
        [
          { kind: "session", marker: { session_id: p.id, session_name: p.name, type: "started", timestamp: p.started_at } } as LogEntry,
          ...prev,
        ].slice(0, 200)
      );
    });
    sock.on("session_ended", (p) => {
      setSessionActive(false);
      setEntries((prev) =>
        [
          { kind: "session", marker: { session_id: p.id, session_name: p.name, type: "ended", timestamp: p.ended_at } } as LogEntry,
          ...prev,
        ].slice(0, 200)
      );
    });
    // Each instructor radio's frames are tagged with its radio_id so the mixed
    // playback stream can carry independent per-radio headset volumes — and so
    // each card's signal meter can track its own radio's receive level.
    sock.onAudio((buf) => {
      const { radioId, pcm } = parseTaggedAudio(buf);
      rxLevels.current[radioId] = Math.max(rxLevels.current[radioId] ?? 0, pcmLevel(pcm));
      audio.current.play(pcm, radioId);
    });
    sock.connect();
    socketRef.current = sock;

    // Warm the mic + playback as soon as the console loads (login is a fresh
    // gesture), so the browser's mic-permission prompt appears now, not on the
    // first PTT. Retry on the first in-view gesture if it was blocked (that
    // fallback also covers playback autoplay).
    const io = audio.current;
    io.prewarm().catch(() => {});
    const enable = () => io.prewarm().catch(() => {});
    window.addEventListener("pointerdown", enable, { once: true });
    window.addEventListener("keydown", enable, { once: true });

    api.instructorRadios().then(setRadios).catch(() => {});
    // Seed the running log from the DB so entries (and session start/stop
    // dividers) recorded before a refresh, a server restart or an update are
    // still listed — with clips playable and transcripts visible. Live
    // broadcasts may land before this resolves, so merge by key with the live
    // entries kept in place.
    Promise.all([api.recentEvents(), api.sessions()]).then(([history, sessions]) => {
      const historyEntries: LogEntry[] = history.map((event) => ({ kind: "event", event }));
      const markers: LogEntry[] = [];
      for (const s of sessions) {
        markers.push({ kind: "session", marker: { session_id: s.id, session_name: s.name, type: "started", timestamp: s.started_at } });
        if (s.ended_at) {
          markers.push({ kind: "session", marker: { session_id: s.id, session_name: s.name, type: "ended", timestamp: s.ended_at } });
        }
      }
      setEntries((prev) => {
        const seenEvents = new Set(prev.filter((e) => e.kind === "event").map((e) => (e as { kind: "event"; event: EventRow }).event.event_id));
        const seenMarkers = new Set(
          prev.filter((e) => e.kind === "session").map((e) => {
            const m = (e as { kind: "session"; marker: SessionLogMarker }).marker;
            return `${m.session_id}-${m.type}`;
          })
        );
        const merged = [
          ...prev,
          ...historyEntries.filter((e) => !seenEvents.has((e as { kind: "event"; event: EventRow }).event.event_id)),
          ...markers.filter((e) => {
            const m = (e as { kind: "session"; marker: SessionLogMarker }).marker;
            return !seenMarkers.has(`${m.session_id}-${m.type}`);
          }),
        ];
        merged.sort((a, b) => timestampOf(b).localeCompare(timestampOf(a)));
        return merged.slice(0, 200);
      });
    }).catch(() => {});
    api.terminals().then((t) => {
      setSessionActive(t.session_active);
      // Restore the running scenario's name after a refresh or a server restart
      // (a resumed session has no session_started broadcast to carry it).
      if (t.session_name) setSessionName(t.session_name);
      setTerminals(t.terminals.filter((x) => !x.is_instructor));
    }).catch(() => {});

    return () => {
      sock.disconnect();
      io.close();
      window.clearInterval(restartPollRef.current);
    };
  }, []);

  // Settings → Restart server flips us into the reconnecting state; the socket
  // close handler then starts polling for the server to come back.
  function enterRestarting() {
    restartingRef.current = true;
    setConn("restarting");
    startRestartPoll();
  }

  async function toggleSession() {
    if (sessionActive) {
      await api.endSession();
      setSessionActive(false);
    } else {
      await api.startSession(sessionName.trim() || "Untitled Exercise");
      setSessionActive(true);
    }
  }

  return (
    <div className="console">
      <ConnectionBanner state={conn} />
      <header className="console__bar">
        <div className="console__brand mono">PIVOT · INSTRUCTOR</div>
        <div className="console__session">
          <input
            className="input mono"
            placeholder="Session name"
            value={sessionName}
            disabled={sessionActive}
            onChange={(e) => setSessionName(e.target.value)}
          />
          <button className={`btn ${sessionActive ? "btn--danger" : "btn--primary"}`} onClick={toggleSession}>
            {sessionActive ? "Stop Session" : "Start Session"}
          </button>
        </div>
        <SevenSegmentClock timezone={timezone} />
        <button className="btn btn--ghost" onClick={onLogout}>Log out</button>
      </header>

      <nav className="console__tabs">
        {(["radios", "monitor", "settings"] as Tab[]).map((t) => (
          <button key={t} className={`tabbtn ${tab === t ? "tabbtn--on" : ""}`} onClick={() => setTab(t)}>
            {t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </nav>

      <main className="console__body">
        {tab === "radios" && <RadiosTab radios={radios} socket={socketRef.current} audio={audio.current} onChange={setRadios} entries={entries} netScenarios={netScenarios} rxLevels={rxLevels} />}
        {tab === "monitor" && <MonitorTab terminals={terminals} />}
        {tab === "settings" && <SettingsTab mustChangePassword={mustChangePassword} onTimezone={onTimezone} socket={socketRef.current} onRestart={enterRestarting} sessionActive={sessionActive} />}
      </main>
    </div>
  );
}

// --------------------------------------------------------------------------- //

const STEP_HZ = 12_500; // tuning step / channel raster (12.5 kHz)
const fmtMHz = (hz: number) => (hz / 1e6).toFixed(4);
function snapToStep(hz: number): number {
  return Math.round(hz / STEP_HZ) * STEP_HZ;
}
// Channel index on the raster — two frequencies on the same channel share a net
// (and therefore share one per-net scenario override).
const netKey = (hz: number) => Math.round(hz / STEP_HZ);
function scenarioFor(netScenarios: NetScenario[], hz: number): NetScenario | undefined {
  return netScenarios.find((s) => netKey(s.freq_hz) === netKey(hz));
}

// The per-radio receive levels live in a ref shared with the socket's audio
// handler; the meters poll and decay it at animation rate without re-renders.
type RxLevels = { current: Record<string, number> };

function RadiosTab({ radios, socket, audio, onChange, entries, netScenarios, rxLevels }: {
  radios: RadioState[]; socket: PivotSocket | null; audio: AudioIO; onChange: (r: RadioState[]) => void;
  entries: LogEntry[]; netScenarios: NetScenario[]; rxLevels: RxLevels;
}) {
  // TX phase per keyed radio (absent = IDLE). Several radios can be keyed at
  // once — the one mic feeds them all, and each runs its own PTT/crypto-sync
  // lifecycle on the server (ptt_* messages carry the radio_id).
  const [phases, setPhases] = useState<Record<string, TxPhase>>({});
  // Radios this console is currently holding keyed: gates duplicate key-downs
  // and decides when the last release stops the shared mic capture. A ref —
  // start/end fire from event handlers and must see the live set.
  const keyed = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!socket) return;
    const setPhase = (id: string, ph: TxPhase) =>
      setPhases((prev) => ({ ...prev, [id]: ph }));
    const clearPhase = (id: string) =>
      setPhases((prev) => { const next = { ...prev }; delete next[id]; return next; });
    const offs = [
      socket.on("ptt_started", (p) => {
        if (!p.radio_id) return;
        setPhase(p.radio_id, p.sync_applies ? "CRYPTO_SYNC" : "TX");
        if (p.sync_applies) playSyncTone();
      }),
      socket.on("secure_tx", (p) => { if (p.radio_id) setPhase(p.radio_id, "SECURE_TX"); }),
      socket.on("ptt_ended", (p) => { if (p.radio_id) clearPhase(p.radio_id); }),
      socket.on("ptt_aborted", (p) => { if (p.radio_id) clearPhase(p.radio_id); }),
      socket.on("tuned", (r) => onChange(updateRadio(radios, r))),
      socket.on("mode_changed", (r) => onChange(updateRadio(radios, r))),
    ];
    return () => offs.forEach((o) => o && o());
  }, [socket, radios, onChange]);

  const startTx = useCallback(async (r: RadioState) => {
    if (!socket || keyed.current.has(r.radio_id)) return;
    playClick();
    keyed.current.add(r.radio_id);
    try {
      await audio.startCapture((pcm) => socket.sendAudio(pcm));
    } catch {
      /* mic blocked: control proceeds, no audio reaches the net */
    }
    // A quick tap can release before the mic finished opening — don't key a
    // radio whose end has already been sent.
    if (!keyed.current.has(r.radio_id)) return;
    socket.instrPttStart(r.radio_id, r.frequency, r.mode);
  }, [socket, audio]);

  const endTx = useCallback((r: RadioState) => {
    if (!socket || !keyed.current.has(r.radio_id)) return;
    keyed.current.delete(r.radio_id);
    playClick(700);
    if (keyed.current.size === 0) audio.stopCapture();
    if (phases[r.radio_id] === "CRYPTO_SYNC") socket.instrPttAbort(r.radio_id);
    else socket.instrPttEnd(r.radio_id);
  }, [socket, phases, audio]);

  // Per-radio PTT hotkey: Shift + the radio's number (§3.4.5). Each card shows
  // its own combo so there is no ambiguity about which radio keys up. Held by
  // the digit's keydown/keyup; e.code stays "Digit#" regardless of Shift.
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (!e.shiftKey || e.repeat || typing(e)) return;
      const m = e.code.match(/^Digit([1-9])$/);
      if (!m) return;
      const r = radios[parseInt(m[1], 10) - 1];
      if (r) { e.preventDefault(); startTx(r); }
    };
    const up = (e: KeyboardEvent) => {
      const m = e.code.match(/^Digit([1-9])$/);
      if (!m) return;
      const r = radios[parseInt(m[1], 10) - 1];
      if (r) { e.preventDefault(); endTx(r); }
    };
    window.addEventListener("keydown", down); window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, [radios, startTx, endTx]);

  async function addRadio() {
    // Omit the frequency so the server applies the operator-configured
    // default start frequency (Settings → Default start frequency).
    const r = await api.addInstructorRadio();
    onChange([...radios, r]);
  }
  async function removeRadio(id: string) {
    await api.removeInstructorRadio(id);
    delete rxLevels.current[id];
    // Local filter for snappiness; the server's instructor_radios broadcast
    // follows with the surviving radios renumbered (Radio 1…N in order).
    onChange(radios.filter((r) => r.radio_id !== id));
  }

  return (
    <div className="radios-layout">
      <div className="instr-radios">
        {radios.map((r, i) => (
          <InstrRadioCard
            key={r.radio_id}
            radio={r}
            index={i + 1}
            socket={socket}
            audio={audio}
            phase={phases[r.radio_id] ?? "IDLE"}
            scenario={scenarioFor(netScenarios, r.frequency_hz)}
            rxLevels={rxLevels}
            onStart={startTx}
            onEnd={endTx}
            onRemove={removeRadio}
          />
        ))}
      </div>
      {/* Below the radios, right-aligned — out of the way, but never scrolled
          out of sight like a grid tile would be when a row is exactly full. */}
      <button className="instr-radios__add" onClick={addRadio}>+ Add Radio</button>
      <LiveLogTab entries={entries} />
    </div>
  );
}

// One instructor radio rendered like the trainee panel: large frequency display
// + tuning, the Plain/Cypher dial, a signal indicator, its own PTT keyed by
// Shift + the card's number (shown on the control so there is no confusion),
// and the channel-effects controls — per-net interference and jamming applied
// to whatever frequency this radio is tuned to (§3.1.5).
function InstrRadioCard({ radio, index, socket, audio, phase, scenario, rxLevels, onStart, onEnd, onRemove }: {
  radio: RadioState; index: number; socket: PivotSocket | null; audio: AudioIO; phase: TxPhase;
  scenario: NetScenario | undefined; rxLevels: RxLevels;
  onStart: (r: RadioState) => void; onEnd: (r: RadioState) => void; onRemove: (id: string) => void;
}) {
  const [entry, setEntry] = useState(fmtMHz(radio.frequency_hz));
  const [volume, setVolume] = useState(() => loadVolume(`instr.${radio.radio_id}`));
  const entryRef = useRef<HTMLInputElement>(null);
  const transmitting = phase !== "IDLE";
  const shortcut = index <= 9 ? `SHIFT + ${index}` : null;

  const interference = scenario?.interference ?? 0;
  const jammed = scenario?.jammed ?? false;
  // This radio's receive-noise toggle (off = monitor the net noiseless). A
  // personal control like volume — the channel itself, and what every other
  // station hears, is shaped by the CHANNEL NOISE controls above instead.
  const rxNoiseOn = radio.rx_noise !== false;

  // This radio's live receive level (shared map, see RadiosTab): the meter
  // shows what the channel actually sounds like — the ambient floor with its
  // crashes and swells, the jam warble, and a transmitting station's voice.
  const readRxLevel = useCallback(
    () => (rxLevels.current[radio.radio_id] = (rxLevels.current[radio.radio_id] ?? 0) * METER_DECAY),
    [rxLevels, radio.radio_id],
  );

  // Local slider value for a smooth drag; the server's broadcast echoes the
  // applied level back through `scenario` (and on retune the box re-syncs to
  // the new channel's setting).
  const [intPct, setIntPct] = useState(Math.round(interference * 100));
  useEffect(() => {
    setIntPct(Math.round(interference * 100));
  }, [interference, radio.frequency_hz]);

  // Apply a per-net override to this radio's current channel ("god mode").
  function setNet(patch: { interference?: number; jammed?: boolean }) {
    api.scenario({ net_scenario: { frequency_hz: radio.frequency_hz, ...patch } }).catch(() => {});
  }

  // Keep the entry box in step with server-confirmed tunes (step buttons,
  // external retunes) without clobbering what the instructor is typing mid-edit.
  useEffect(() => { setEntry(fmtMHz(radio.frequency_hz)); }, [radio.frequency_hz]);

  // Apply this radio's saved headset volume to the shared player (and on change).
  useEffect(() => { audio.setVolume(volume, radio.radio_id); }, [audio, radio.radio_id, volume]);
  function changeVolume(v: number) {
    setVolume(v);
    saveVolume(`instr.${radio.radio_id}`, v);
  }

  function tuneTo(hz: number) {
    const snapped = Math.max(1.6e6, Math.min(3e9, snapToStep(hz)));
    socket?.instrTune(radio.radio_id, `${fmtMHz(snapped)} MHz`);
  }
  // Confirm a typed frequency and hand focus back so the Shift+# PTT keys up
  // instead of typing into the box.
  function confirmEntry() {
    const v = parseFloat(entry);
    if (!isNaN(v)) tuneTo(v * 1e6);
    entryRef.current?.blur();
  }

  return (
    <section className="card instr-radio">
      <div className="instr-radio__info">
        <div className="instr-radio__head">
          <span className="instr-radio__num mono" aria-hidden>{index}</span>
          <span className="instr-radio__name mono">{radio.name}</span>
          <button className="btn btn--ghost instr-radio__remove" aria-label="Remove radio" title="Remove radio"
            onClick={() => onRemove(radio.radio_id)} disabled={transmitting}>✕</button>
        </div>

        <div className="freq">
          <div className="freq__display mono">{fmtMHz(radio.frequency_hz)}<span className="freq__unit">MHz</span></div>
          <div className="freq__controls">
            <button className="btn btn--step" aria-label="Decrease frequency"
              onClick={() => tuneTo(radio.frequency_hz - STEP_HZ)} disabled={transmitting}>▼</button>
            <input ref={entryRef} className="input mono freq__entry" aria-label="Frequency in MHz"
              value={entry} disabled={transmitting}
              onChange={(e) => setEntry(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") confirmEntry(); }} />
            <button className="btn btn--step" aria-label="Increase frequency"
              onClick={() => tuneTo(radio.frequency_hz + STEP_HZ)} disabled={transmitting}>▲</button>
            <button className="btn btn--primary" onClick={confirmEntry} disabled={transmitting}>Tune</button>
          </div>
        </div>

        <div className="radio__row">
          <ModeDial
            mode={radio.mode}
            onToggle={() => socket?.instrMode(radio.radio_id, radio.mode === "Cypher" ? "Plain" : "Cypher")}
            disabled={transmitting}
            title="Plain / Cypher (persists across retuning)"
          />
          <SignalMeter label={`SIGNAL · ${radio.band_region}`} read={readRxLevel} />
        </div>

        <div className={`neteffects ${jammed || intPct !== 0 ? "neteffects--active" : ""}`}>
          <span className="neteffects__label">
            CHANNEL NOISE{jammed ? " · JAMMED" : intPct > 0 ? ` · +${intPct}%` : intPct < 0 ? ` · CLEANED ${-intPct}%` : " · BASELINE"}
          </span>
          <div className="row gap">
            <input
              type="range" min={-100} max={100} value={intPct} list={`net-baseline-${radio.radio_id}`}
              aria-label="Noise offset on this channel (0 = natural baseline)"
              title="Noise on this channel: 0 is the frequency's natural baseline; raise it to induce interference, lower it to temporarily clean the channel up"
              onChange={(e) => {
                const v = +e.target.value;
                setIntPct(v);
                setNet({ interference: v / 100 });
              }}
              onDoubleClick={() => { setIntPct(0); setNet({ interference: 0 }); }}
            />
            <datalist id={`net-baseline-${radio.radio_id}`}>
              <option value={0} label="baseline" />
            </datalist>
            <button
              className={`btn ${jammed ? "btn--danger" : ""}`}
              title="Jam this channel (a wall of jammer noise; trainees must change frequency)"
              onClick={() => setNet({ jammed: !jammed })}
            >
              {jammed ? "JAMMING" : "Jam"}
            </button>
          </div>
        </div>

        <div className="rxctl">
          <VolumeSlider value={volume} onChange={changeVolume} />
          <button
            className={`btn ${rxNoiseOn ? "" : "btn--warn"}`}
            title="Channel noise on this radio's receive only — turn it off to monitor this net unhindered. Every other station still hears the channel noise (shape the net itself with the CHANNEL NOISE controls)."
            onClick={() => socket?.instrRxNoise(radio.radio_id, !rxNoiseOn)}
          >
            {rxNoiseOn ? "RX Noise: On" : "RX NOISE OFF"}
          </button>
        </div>
      </div>

      <button
        className={`ptt ptt--${phase.toLowerCase()}`}
        onMouseDown={() => onStart(radio)}
        onMouseUp={() => onEnd(radio)}
        onMouseLeave={() => transmitting && onEnd(radio)}
        onTouchStart={(e) => { e.preventDefault(); onStart(radio); }}
        onTouchEnd={(e) => { e.preventDefault(); onEnd(radio); }}
      >
        <span className="ptt__state">{phaseLabel(phase)}</span>
        <span className="ptt__hint">{shortcut ? `HOLD · ${shortcut}` : "HOLD"}</span>
      </button>
    </section>
  );
}

function LiveLogTab({ entries }: { entries: LogEntry[] }) {
  const [audio] = useState(() => new Audio());
  // Two independent playbacks of the one stored recording: "clean" is the raw
  // pre-DSP capture (no noise); "dirty" re-renders it through the original DSP
  // profile so the instructor hears it as it was received over the air.
  function play(ev: EventRow, mode: "clean" | "dirty") {
    audio.pause();
    audio.src = api.eventAudioUrl(ev.event_id, mode, "cypher");
    audio.play().catch(() => {});
  }
  return (
    <section className="card pad logcard">
      <h3>Running Event Log</h3>
      {entries.length === 0 && <p className="muted">Transmissions will appear here as they happen.</p>}
      <div className="log">
        {entries.map((entry) => {
          if (entry.kind === "session") {
            const m = entry.marker;
            return (
              <div className="logrow logrow--session" key={`session-${m.session_id}-${m.type}`}>
                <span className="mono muted">{m.timestamp.slice(11, 19)}</span>
                <span className="logrow__session-label">
                  Session “{m.session_name}” {m.type === "started" ? "started" : "stopped"}
                </span>
              </div>
            );
          }
          const ev = entry.event;
          const low = ev.transcription_confidence != null && ev.transcription_confidence < 0.8;
          return (
            <div className="logrow" key={ev.event_id}>
              <span className="event__play-group">
                <button className="event__play" onClick={() => play(ev, "clean")} aria-label="Play without noise" title="Play without noise">▶</button>
                <button className="event__play" onClick={() => play(ev, "dirty")} aria-label="Play with noise (as heard)" title="Play with noise (as heard)">📻</button>
              </span>
              <span className="mono muted">{ev.timestamp_start.slice(11, 19)}</span>
              <span className="mono">{ev.trainee_name}</span>
              <span className="mono">{ev.frequency}</span>
              <span title={ev.tx_mode}>{ev.tx_mode === "Cypher" ? "🔒" : "◌"}</span>
              <span className={`event__aud aud--${ev.audibility.split("-")[0].toLowerCase()}`}>{ev.audibility}</span>
              <span className={`logtext ${low ? "text--amber" : ""} ${!ev.transcription ? "text--none" : ""}`}>
                {ev.jammed && (
                  <span
                    className="event__jammed"
                    title={`Captured while its channel was jammed${
                      ev.snr_db != null ? ` (SNR ${Math.round(ev.snr_db)} dB)` : ""
                    }. "Play with noise" re-renders it as a wall of jammer noise.`}
                  >
                    JAMMED
                  </span>
                )}
                {ev.transcription || (ev.transcription_status === "Pending" ? "transcribing…" : "—")}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function MonitorTab({ terminals }: { terminals: Terminal[] }) {
  async function kick(id: string) { await api.scenario({ kick_trainee_id: id }); }
  return (
    <section className="card pad">
      <h3>Connected Terminals ({terminals.length})</h3>
      <table className="tbl">
        <thead><tr><th>Callsign</th><th>Frequency</th><th>Mode</th><th>Status</th><th>Last</th><th></th></tr></thead>
        <tbody>
          {terminals.map((t) => (
            <tr key={t.radio_id}>
              <td className="mono">{t.name}</td>
              <td className="mono">{t.frequency} <small className="muted">{t.band_region}</small></td>
              <td>{t.mode}</td>
              <td>{t.status}</td>
              <td className="mono muted">{t.last_activity.slice(11, 19)}</td>
              <td><button className="btn btn--ghost" onClick={() => kick(t.radio_id)}>Kick</button></td>
            </tr>
          ))}
          {terminals.length === 0 && <tr><td colSpan={6} className="muted">No trainees connected.</td></tr>}
        </tbody>
      </table>
    </section>
  );
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

// Where recordings live + a one-click "open it" for the instructor who can't
// find the WAVs on disk. The server host opens its own file manager; when it
// can't (headless), we fall back to showing the absolute path to copy.
function RecordingsCard() {
  const [path, setPath] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api.recordingsLocation().then((r) => setPath(r.path)).catch(() => {});
  }, []);

  async function openFolder() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.openRecordingsFolder();
      setPath(r.path);
      setMsg(
        r.opened
          ? "Opened on the machine running PIVOT."
          : "Couldn’t open a file manager here — browse to the path below on the PIVOT server."
      );
    } catch {
      setMsg("Couldn’t open the folder. Use the path below.");
    } finally {
      setBusy(false);
    }
  }

  async function copyPath() {
    if (!path) return;
    try {
      await navigator.clipboard.writeText(path);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked (insecure origin) — the path is on screen to copy */
    }
  }

  return (
    <section className="card pad">
      <h3>Recordings</h3>
      <p className="muted" style={{ marginTop: 0 }}>
        Per-transmission WAVs, named by session and time so they’re easy to find
        in a file browser.
      </p>
      <div className="row gap" style={{ flexWrap: "wrap" }}>
        <button className="btn btn--primary" onClick={openFolder} disabled={busy}>
          {busy ? "Opening…" : "Open recordings folder"}
        </button>
        <button className="btn" onClick={copyPath} disabled={!path}>
          {copied ? "Copied ✓" : "Copy path"}
        </button>
      </div>
      {path && (
        <div className="muted mono mt" style={{ wordBreak: "break-all" }}>{path}</div>
      )}
      {msg && <p className="muted mt" style={{ fontSize: "0.85em" }}>{msg}</p>}
    </section>
  );
}

function SettingsTab({ mustChangePassword, onTimezone, socket, onRestart, sessionActive }: {
  mustChangePassword: boolean; onTimezone: (tz: string) => void; socket: PivotSocket | null;
  onRestart: () => void; sessionActive: boolean;
}) {
  const [cfg, setCfg] = useState<Record<string, any>>({});
  const [saved, setSaved] = useState(false);
  const [pw, setPw] = useState({ current: "", next: "" });
  const [pwMsg, setPwMsg] = useState("");
  const timezoneOptions = useMemo(getTimezoneOptions, []);
  const [upd, setUpd] = useState<UpdateStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [applying, setApplying] = useState<string | null>(null);
  const [staged, setStaged] = useState<string | null>(null);
  const [applyErr, setApplyErr] = useState<string | null>(null);
  const [restartErr, setRestartErr] = useState<string | null>(null);
  const [showDowngrade, setShowDowngrade] = useState(false);
  // Re-open the version lists while an update is staged, to swap the pending
  // version for a different pick before restarting.
  const [showChoose, setShowChoose] = useState(false);
  // The version awaiting restart: the server's fresh staged_tag is the truth;
  // local `staged` covers the moment right after a manual apply, and
  // auto_staged is a fallback for older payload shapes.
  const stagedTag = staged || upd?.staged_tag || upd?.auto_staged || null;
  // Versions actually stored on disk (instant rollback / deletable), loaded
  // lazily when the downgrade pane is opened. Distinct from older *releases*,
  // which re-download.
  const [retained, setRetained] = useState<{ tag: string; bytes: number }[] | null>(null);

  async function restart(force: boolean) {
    setRestartErr(null);
    try {
      await api.restartServer(force);
      onRestart();
    } catch (e: any) {
      const msg = String(e?.message ?? "");
      // 409 = a session is running; offer to force.
      if (msg.startsWith("409")) {
        setRestartErr("A session is running. Use “Restart anyway” to apply now and disconnect trainees.");
      } else {
        setRestartErr(msg || "Restart failed.");
      }
    }
  }

  function absorb(result: UpdateStatus) {
    setUpd(result);
    // staged_tag is the truth (read fresh from the pending marker server-side):
    // the exact version awaiting restart, whether it was chosen manually or
    // auto-staged. auto_staged is kept as a fallback for older payloads only.
    if (result.staged_tag) setStaged(result.staged_tag);
    else if (result.auto_staged) setStaged(result.auto_staged);
    if (result.auto_update_error) setApplyErr(result.auto_update_error);
  }

  // The background service checks out-of-band; show its cached status on mount
  // and update live as it broadcasts (no network wait, always current). If the
  // service has never checked (fresh boot), kick one refresh so the card is
  // populated without the instructor having to press anything.
  useEffect(() => {
    api.checkUpdates()
      .then((snap) => {
        absorb(snap);
        if (!snap.last_checked && !snap.checking) {
          setChecking(true);
          api.refreshUpdates().then(absorb).catch(() => {}).finally(() => setChecking(false));
        }
      })
      .catch(() => {});
    if (!socket) return;
    const off = socket.on("update_status", (snap: UpdateStatus) => absorb(snap));
    return () => { off(); };
  }, [socket]);

  // "Check now" forces a synchronous re-check rather than reading the cache.
  async function checkUpdates() {
    setChecking(true);
    setApplyErr(null);
    try {
      absorb(await api.refreshUpdates());
    } finally {
      setChecking(false);
    }
  }

  async function applyUpdate(a: ReleaseInfo) {
    // Downgrades can cross a DB schema migration — confirm first.
    if (a.standing === "older" &&
        !window.confirm(`Install ${a.tag}? This is a DOWNGRADE from the running version. ` +
          `If it crosses a database change, back up your data first. It applies on restart.`)) {
      return;
    }
    setApplying(a.tag);
    setApplyErr(null);
    try {
      await api.applyUpdate(a.tag, a.asset_url, a.sha256_url, a.sig_url, a.asset_name);
      // Verified + staged; the swap finishes on the next restart. This replaces
      // any previously staged version (the last explicit choice wins).
      setStaged(a.tag);
      setShowChoose(false);
    } catch (e: any) {
      setApplyErr(e?.message ?? "Download failed");
    } finally {
      setApplying(null);
    }
  }

  // Instant offline rollback to a retained version (no re-download).
  async function rollback(tag: string) {
    if (!window.confirm(`Roll back to ${tag}? It applies on the next restart. ` +
        `If the downgrade crosses a database change, back up your data first.`)) {
      return;
    }
    setApplying(`rollback:${tag}`);
    setApplyErr(null);
    try {
      const res = await api.rollbackUpdate(tag);
      setStaged(res.tag);
      setShowChoose(false);
    } catch (e: any) {
      setApplyErr(e?.message ?? "Rollback failed");
    } finally {
      setApplying(null);
    }
  }

  // Load the on-disk version list when the downgrade pane is first opened (and
  // after a delete), so the size walk only runs when the instructor looks.
  useEffect(() => {
    if (!showDowngrade) return;
    api.retainedVersions().then((r) => setRetained(r.retained)).catch(() => setRetained([]));
  }, [showDowngrade]);

  async function deleteRetained(tag: string) {
    if (!window.confirm(`Delete stored version ${tag} from disk? ` +
        `You can re-download it later, but instant rollback to it will no longer be available.`)) {
      return;
    }
    setApplying(`delete:${tag}`);
    setApplyErr(null);
    try {
      const res = await api.deleteRetained(tag);
      setRetained(res.retained);
    } catch (e: any) {
      setApplyErr(e?.message ?? "Delete failed");
    } finally {
      setApplying(null);
    }
  }

  useEffect(() => { api.getConfig().then(setCfg).catch(() => {}); }, []);
  const set = (k: string, v: any) => setCfg((c) => ({ ...c, [k]: v }));

  // Live crypto kill switch (formerly on the Scenario tab). Initialised from
  // the band profile and applied immediately — it is a scenario action, not a
  // saved setting.
  const [cryptoOn, setCryptoOn] = useState(true);
  useEffect(() => {
    api.bandProfile().then((p) => setCryptoOn(!!p.crypto_enabled)).catch(() => {});
  }, []);

  async function save() {
    const keys = ["whisper_model", "whisper_compute_type", "transcription_confidence_threshold",
      "transcription_skip_under_seconds", "display_timezone", "crypto_delay_ms",
      "default_frequency_hz", "update_channel", "auto_update", "update_check_on_startup"];
    const updates: Record<string, unknown> = {};
    keys.forEach((k) => (updates[k] = cfg[k]));
    const { applied } = await api.updateSettings(updates);
    // Reflect any server-side normalisation (e.g. the start frequency snapped
    // to the channel raster) back into the form.
    setCfg((c) => ({ ...c, ...applied }));
    onTimezone(String(cfg.display_timezone || "UTC"));
    setSaved(true); setTimeout(() => setSaved(false), 1500);
  }

  async function changePassword() {
    setPwMsg("");
    try {
      await api.changePassword(pw.current, pw.next);
      setPwMsg("Password changed. Use it next time you log in.");
      setPw({ current: "", next: "" });
    } catch {
      setPwMsg("Could not change password (check the current one).");
    }
  }

  return (
    <div className="grid2">
      <section className="card pad">
        <h3>Settings</h3>
        <Field label="Update channel">
          <select className="input" value={cfg.update_channel || "stable"}
            onChange={(e) => set("update_channel", e.target.value)}>
            <option value="stable">Stable only</option>
            <option value="include_prereleases">Include prereleases (test builds)</option>
          </select>
        </Field>
        <label className="row gap" style={{ marginBottom: 12 }}>
          <input type="checkbox" checked={!!cfg.auto_update}
            onChange={(e) => set("auto_update", e.target.checked)} />
          Automatically update to the newest version on the chosen channel
        </label>
        <Field label="Whisper model">
          <select className="input" value={cfg.whisper_model || "small"} onChange={(e) => set("whisper_model", e.target.value)}>
            {["tiny", "base", "small", "medium", "large-v3"].map((m) => <option key={m}>{m}</option>)}
          </select>
        </Field>
        <Field label="Compute type">
          <select className="input" value={cfg.whisper_compute_type || "auto"} onChange={(e) => set("whisper_compute_type", e.target.value)}>
            {["auto", "int8", "int8_float16", "float16"].map((m) => <option key={m}>{m}</option>)}
          </select>
        </Field>
        <Field label="Amber confidence threshold">
          <input className="input" type="number" step="0.05" min="0" max="1"
            value={cfg.transcription_confidence_threshold ?? 0.8}
            onChange={(e) => set("transcription_confidence_threshold", parseFloat(e.target.value))} />
        </Field>
        <label className="row gap" style={{ marginBottom: 12 }}>
          <input type="checkbox" checked={cryptoOn}
            onChange={(e) => {
              setCryptoOn(e.target.checked);
              api.scenario({ crypto_enabled: e.target.checked }).catch(() => {});
            }} />
          Crypto available to all radios (applies immediately)
        </label>
        <Field label="Crypto sync delay (ms)">
          <input className="input" type="number" step="100" min="0"
            value={cfg.crypto_delay_ms ?? 1500} onChange={(e) => set("crypto_delay_ms", parseInt(e.target.value))} />
        </Field>
        <Field label="Default start frequency (MHz)">
          <input className="input mono" type="number" step="0.0125" min="0"
            value={((cfg.default_frequency_hz ?? 7_000_000) as number) / 1e6}
            onChange={(e) => {
              const mhz = parseFloat(e.target.value);
              set("default_frequency_hz", isNaN(mhz) ? cfg.default_frequency_hz : mhz * 1e6);
            }}
            // Radios only tune to the 12.5 kHz raster, so snap on blur to show
            // the value that will actually be applied.
            onBlur={() => {
              const hz = (cfg.default_frequency_hz ?? 7_000_000) as number;
              set("default_frequency_hz", snapToStep(hz));
            }} />
        </Field>
        <Field label="Display timezone">
          <select className="input mono" value={cfg.display_timezone || "UTC"} onChange={(e) => set("display_timezone", e.target.value)}>
            {timezoneOptions.includes(cfg.display_timezone || "UTC") ? null : (
              <option value={cfg.display_timezone || "UTC"}>{cfg.display_timezone || "UTC"}</option>
            )}
            {timezoneOptions.map((tz) => <option key={tz} value={tz}>{tz}</option>)}
          </select>
        </Field>
        <button className="btn btn--primary" onClick={save}>{saved ? "Saved ✓" : "Save Settings"}</button>
      </section>

      <RecordingsCard />

      <section className="card pad">
        <h3>Instructor Password</h3>
        {mustChangePassword && <p className="login__hint">You are using the default password. Please change it.</p>}
        <Field label="Current password">
          <input className="input" type="password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} />
        </Field>
        <Field label="New password">
          <input className="input" type="password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} />
        </Field>
        <button className="btn btn--primary" disabled={pw.next.length < 4} onClick={changePassword}>Change Password</button>
        {pwMsg && <p className="muted mt">{pwMsg}</p>}
      </section>

      <section className="card pad">
        <div className="row between" style={{ alignItems: "center" }}>
          <h3 style={{ margin: 0 }}>Updates</h3>
          <button className="btn" onClick={checkUpdates}
            disabled={checking || upd?.checking || applying !== null}>
            {checking || upd?.checking ? "Checking…" : "Check now"}
          </button>
        </div>

        {/* Always-visible facts: what's running, on which channel, last check. */}
        <div className="muted mono mt">
          {upd ? `v${upd.current_version}` : "—"}
          {" · "}{upd?.channel === "include_prereleases" ? "prereleases" : "stable channel"}
          {" · auto-update "}{upd?.auto_update ? "on" : "off"}
        </div>
        <div className="muted mt" style={{ fontSize: "0.85em" }}>
          {checking || upd?.checking
            ? "Checking GitHub…"
            : upd?.last_checked
              ? `Last checked ${relTime(upd.last_checked)}`
              : "Not checked yet"}
        </div>

        {/* One primary status line — single source of truth for the headline. */}
        {upd && (() => {
          if (stagedTag)
            return <p className="mt" style={{ fontWeight: 600 }}>{stagedTag} ready — restart PIVOT to apply ✓</p>;
          if (upd.auto_state === "downloading")
            return <p className="muted mt">⟳ {upd.auto_message || "Downloading update…"}</p>;
          if (upd.auto_state === "deferred_session_active")
            return <p className="mt" style={{ fontWeight: 600 }}>{upd.auto_message || "Update deferred until the session ends."}</p>;
          if (upd.auto_state === "error")
            return <p className="login__hint mt">Auto-update failed: {upd.auto_message}</p>;
          if (!upd.reachable && !upd.checking)
            return <p className="login__hint mt">
              GitHub unreachable{upd.error ? <> — <code>{upd.error}</code></> : ""}.{" "}
              If your browser can reach the internet but this fails, the cause is
              usually a proxy, firewall or TLS-inspecting certificate that this
              server process doesn't see (browsers use the OS's settings; this
              check doesn't) — check the server's console/log for the same
              message, or use offline import.
            </p>;
          if (upd.reachable && upd.available.length === 0)
            return <p className="mt" style={{ fontWeight: 600 }}>You’re up to date.</p>;
          if (upd.reachable && upd.available.length > 0)
            return <p className="mt" style={{ fontWeight: 600 }}>
              {upd.available.length} newer release{upd.available.length > 1 ? "s" : ""} available
              {upd.auto_update ? " — will install automatically when no session is running." : ":"}
            </p>;
          return null;
        })()}

        {/* Something is staged: the choice isn't locked in until the restart, so
            offer to pick a different version — staging the new pick replaces the
            pending one (the server never auto-overwrites it the other way). */}
        {upd && stagedTag && (
          <button className="btn btn--ghost mt" onClick={() => setShowChoose((s) => !s)}>
            {showChoose ? "Keep the staged version" : "Choose a different version…"}
          </button>
        )}

        {/* Per-release rows: shown when nothing is staged yet, or when the
            instructor wants to replace the staged version with another one. */}
        {upd && (!stagedTag || showChoose) && upd.reachable && upd.available.map((a) => (
          <div className="row between mt" key={a.tag}>
            <span className="mono">
              {a.tag}{a.prerelease ? " · prerelease" : ""}{a.tag === stagedTag ? " · staged" : ""}
            </span>
            {applying === a.tag ? (
              <span className="muted">Downloading…</span>
            ) : a.tag === stagedTag ? (
              <span className="muted">Staged — restart to apply</span>
            ) : a.has_asset ? (
              <button className="btn btn--primary" onClick={() => applyUpdate(a)}
                disabled={applying !== null}>
                Download &amp; install
              </button>
            ) : (
              <span className="muted">No build for this platform</span>
            )}
          </div>
        ))}
        {applyErr && <p className="login__hint mt">{applyErr}</p>}

        {/* Downgrade / recovery: instant rollback to the retained previous build
            (no re-download), plus the full version list so a bad update never
            blocks training. */}
        {upd && (!stagedTag || showChoose) && (
          <div className="mt">
            <button className="btn btn--ghost" onClick={() => setShowDowngrade((s) => !s)}>
              {showDowngrade ? "Hide downgrade options" : "Downgrade / recovery…"}
            </button>
            {showDowngrade && (
              <div className="mt">
                {/* Stored on disk: instant rollback, no download — and deletable
                    to free space. These are the versions actually present in the
                    install's versions folder (unlike the re-download list below). */}
                <p className="muted" style={{ fontSize: "0.85em" }}>
                  Stored on disk (instant rollback, no download):
                </p>
                {retained === null ? (
                  <p className="muted mt" style={{ fontSize: "0.85em" }}>Loading…</p>
                ) : retained.length === 0 ? (
                  <p className="muted mt" style={{ fontSize: "0.85em" }}>
                    No versions stored on disk yet. One is kept each time you update.
                  </p>
                ) : (
                  retained.map((v) => (
                    <div className="row between mt" key={v.tag} style={{ alignItems: "center" }}>
                      <span className="mono">{v.tag} · {fmtBytes(v.bytes)}</span>
                      <span className="row gap" style={{ alignItems: "center" }}>
                        {applying === `rollback:${v.tag}` ? (
                          <span className="muted">Staging…</span>
                        ) : (
                          <button className="btn btn--danger" onClick={() => rollback(v.tag)}
                            disabled={applying !== null}>
                            Roll back
                          </button>
                        )}
                        {applying === `delete:${v.tag}` ? (
                          <span className="muted">Deleting…</span>
                        ) : (
                          <button className="btn btn--ghost" onClick={() => deleteRetained(v.tag)}
                            disabled={applying !== null} title="Delete from disk to free space">
                            Delete
                          </button>
                        )}
                      </span>
                    </div>
                  ))
                )}
                <p className="muted mt" style={{ fontSize: "0.85em" }}>
                  Or install any earlier version (re-downloads &amp; verifies it):
                </p>
                {(upd.releases || []).filter((r) => r.standing === "older").map((a) => (
                  <div className="row between mt" key={a.tag}>
                    <span className="mono">
                      {a.tag}{a.prerelease ? " · prerelease" : ""}{a.tag === stagedTag ? " · staged" : ""}
                    </span>
                    {applying === a.tag ? (
                      <span className="muted">Downloading…</span>
                    ) : a.tag === stagedTag ? (
                      <span className="muted">Staged — restart to apply</span>
                    ) : a.has_asset ? (
                      <button className="btn" onClick={() => applyUpdate(a)} disabled={applying !== null}>
                        Install this version
                      </button>
                    ) : (
                      <span className="muted">No build for this platform</span>
                    )}
                  </div>
                ))}
                {(upd.releases || []).filter((r) => r.standing === "older").length === 0 && (
                  <p className="muted mt" style={{ fontSize: "0.85em" }}>
                    No earlier versions available to download.
                  </p>
                )}
                <p className="muted mt" style={{ fontSize: "0.8em" }}>
                  Tip: if a bad update won’t even start, run
                  <span className="mono"> PIVOT-Tactical --rollback </span>
                  from the install folder to recover.
                </p>
              </div>
            )}
          </div>
        )}

        {/* Restart from the browser: applies a staged update on the way back up,
            and is useful on its own. */}
        {(() => {
          const hasStaged = !!stagedTag;
          return (
            <div className="row gap mt" style={{ alignItems: "center" }}>
              <button
                className={`btn ${hasStaged ? "btn--primary" : ""}`}
                onClick={() => restart(false)}
              >
                {hasStaged ? "Restart now to apply" : "Restart server"}
              </button>
              {restartErr && (
                <button className="btn btn--danger" onClick={() => restart(true)}>
                  Restart anyway
                </button>
              )}
            </div>
          );
        })()}
        {restartErr && <p className="login__hint mt">{restartErr}</p>}
        {sessionActive && !restartErr && (
          <p className="muted mt" style={{ fontSize: "0.85em" }}>
            A session is running — restarting will disconnect trainees, so it’s guarded.
          </p>
        )}

        {/* One concise mechanism note (not repeated above). */}
        <p className="muted mt" style={{ fontSize: "0.85em" }}>
          Updates are verified (checksum + signature), staged, and applied on the
          next restart — out-of-band, never mid-session. Air-gapped sites can use
          offline import.
        </p>
      </section>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}

// Compact "x ago" for the last-checked timestamp; falls back to a local date.
function relTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "just now";
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 45) return "just now";
  if (secs < 90) return "a minute ago";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hour${hrs > 1 ? "s" : ""} ago`;
  return new Date(iso).toLocaleString();
}

function updateRadio(radios: RadioState[], r: RadioState): RadioState[] {
  return radios.map((x) => (x.radio_id === r.radio_id ? r : x));
}
function phaseLabel(p: TxPhase) {
  return p === "CRYPTO_SYNC" ? "CRYPTO SYNC…" : p === "SECURE_TX" ? "SECURE TX" : p === "TX" ? "TX" : "PUSH TO TALK";
}
function typing(e: KeyboardEvent) {
  const el = e.target as HTMLElement;
  return el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT");
}
