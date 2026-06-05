import { useCallback, useEffect, useRef, useState } from "react";
import { api, getToken } from "../api";
import { playClick, playSyncTone } from "../audio";
import { SevenSegmentClock } from "../components/SevenSegmentClock";
import type { EventRow, RadioState, Terminal, TxPhase } from "../types";
import { PivotSocket } from "../ws";

type Tab = "radios" | "log" | "monitor" | "scenario" | "settings";

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
  const [events, setEvents] = useState<EventRow[]>([]);
  const [sessionActive, setSessionActive] = useState(false);
  const [sessionName, setSessionName] = useState("");
  const socketRef = useRef<PivotSocket | null>(null);

  useEffect(() => {
    const sock = new PivotSocket({ token: getToken() || "" });
    sock.on("instructor_radios", (p) => setRadios(p));
    sock.on("terminal_update", (p) => setTerminals((p.terminals || []).filter((t: Terminal) => !t.is_instructor)));
    sock.on("event_logged", (ev) => setEvents((prev) => [ev, ...prev].slice(0, 200)));
    sock.on("transcription_updated", (ev) =>
      setEvents((prev) => prev.map((e) => (e.event_id === ev.event_id ? { ...e, ...ev } : e)))
    );
    sock.on("session_started", () => setSessionActive(true));
    sock.on("session_ended", () => setSessionActive(false));
    sock.connect();
    socketRef.current = sock;

    api.instructorRadios().then(setRadios).catch(() => {});
    api.terminals().then((t) => {
      setSessionActive(t.session_active);
      setTerminals(t.terminals.filter((x) => !x.is_instructor));
    }).catch(() => {});

    return () => sock.disconnect();
  }, []);

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
        {(["radios", "log", "monitor", "scenario", "settings"] as Tab[]).map((t) => (
          <button key={t} className={`tabbtn ${tab === t ? "tabbtn--on" : ""}`} onClick={() => setTab(t)}>
            {t === "log" ? "Live Log" : t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </nav>

      <main className="console__body">
        {tab === "radios" && <RadiosTab radios={radios} socket={socketRef.current} onChange={setRadios} />}
        {tab === "log" && <LiveLogTab events={events} />}
        {tab === "monitor" && <MonitorTab terminals={terminals} />}
        {tab === "scenario" && <ScenarioTab />}
        {tab === "settings" && <SettingsTab mustChangePassword={mustChangePassword} onTimezone={onTimezone} />}
      </main>
    </div>
  );
}

// --------------------------------------------------------------------------- //

const fmtMHz = (hz: number) => (hz / 1e6).toFixed(3);

function RadiosTab({ radios, socket, onChange }: {
  radios: RadioState[]; socket: PivotSocket | null; onChange: (r: RadioState[]) => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [phase, setPhase] = useState<TxPhase>("IDLE");
  const [newFreq, setNewFreq] = useState("30.000");
  const active = radios.find((r) => r.radio_id === selected) || radios[0];

  useEffect(() => {
    if (!socket) return;
    const offs = [
      socket.on("ptt_started", (p) => { setPhase(p.sync_applies ? "CRYPTO_SYNC" : "TX"); if (p.sync_applies) playSyncTone(); }),
      socket.on("secure_tx", () => setPhase("SECURE_TX")),
      socket.on("ptt_ended", () => setPhase("IDLE")),
      socket.on("ptt_aborted", () => setPhase("IDLE")),
      socket.on("tuned", (r) => onChange(updateRadio(radios, r))),
      socket.on("mode_changed", (r) => onChange(updateRadio(radios, r))),
    ];
    return () => offs.forEach((o) => o && o());
  }, [socket, radios, onChange]);

  const startTx = useCallback(() => {
    if (!socket || !active || phase !== "IDLE") return;
    playClick();
    socket.instrPttStart(active.radio_id, active.frequency, active.mode);
  }, [socket, active, phase]);

  const endTx = useCallback(() => {
    if (!socket || !active) return;
    playClick(700);
    if (phase === "CRYPTO_SYNC") socket.instrPttAbort(active.radio_id);
    else socket.instrPttEnd(active.radio_id);
    setPhase("IDLE");
  }, [socket, active, phase]);

  useEffect(() => {
    const down = (e: KeyboardEvent) => { if (e.code === "Space" && !e.repeat && !typing(e)) { e.preventDefault(); startTx(); } };
    const up = (e: KeyboardEvent) => { if (e.code === "Space" && !typing(e)) { e.preventDefault(); endTx(); } };
    window.addEventListener("keydown", down); window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, [startTx, endTx]);

  async function addRadio() {
    const r = await api.addInstructorRadio(`${parseFloat(newFreq) || 30} MHz`);
    onChange([...radios, r]);
  }
  async function removeRadio(id: string) {
    await api.removeInstructorRadio(id);
    onChange(radios.filter((r) => r.radio_id !== id));
  }

  return (
    <div className="grid2">
      <section className="card pad">
        <div className="row between">
          <h3>Instructor Radios</h3>
          <div className="row">
            <input className="input mono w120" value={newFreq} onChange={(e) => setNewFreq(e.target.value)} />
            <button className="btn btn--primary" onClick={addRadio}>Add MHz</button>
          </div>
        </div>
        <table className="tbl">
          <thead><tr><th></th><th>Radio</th><th>Frequency</th><th>Region</th><th>Mode</th><th></th></tr></thead>
          <tbody>
            {radios.map((r) => (
              <tr key={r.radio_id} className={active?.radio_id === r.radio_id ? "row--sel" : ""}>
                <td><input type="radio" checked={active?.radio_id === r.radio_id} onChange={() => setSelected(r.radio_id)} /></td>
                <td className="mono">{r.name}</td>
                <td className="mono">
                  <input className="input mono w110" defaultValue={fmtMHz(r.frequency_hz)}
                    onKeyDown={(e) => { if (e.key === "Enter") socket?.instrTune(r.radio_id, `${(e.target as HTMLInputElement).value} MHz`); }} />
                </td>
                <td>{r.band_region}</td>
                <td>
                  <button className={`toggle toggle--sm ${r.mode === "Cypher" ? "toggle--cypher" : "toggle--plain"}`}
                    onClick={() => socket?.instrMode(r.radio_id, r.mode === "Cypher" ? "Plain" : "Cypher")}>
                    {r.mode === "Cypher" ? "🔒" : "◌"} {r.mode}
                  </button>
                </td>
                <td><button className="btn btn--ghost" onClick={() => removeRadio(r.radio_id)}>✕</button></td>
              </tr>
            ))}
            {radios.length === 0 && <tr><td colSpan={6} className="muted">No instructor radios. Add one above.</td></tr>}
          </tbody>
        </table>
      </section>

      <section className="card pad center">
        <h3>Transmit</h3>
        <div className="muted mono">{active ? `${active.name} · ${active.frequency} · ${active.mode}` : "Select a radio"}</div>
        <button className={`ptt ptt--${phase.toLowerCase()}`} disabled={!active}
          onMouseDown={startTx} onMouseUp={endTx} onMouseLeave={() => phase !== "IDLE" && endTx()}>
          <span className="ptt__state">{phaseLabel(phase)}</span>
          <span className="ptt__hint">HOLD / SPACE</span>
        </button>
      </section>
    </div>
  );
}

function LiveLogTab({ events }: { events: EventRow[] }) {
  const [audio] = useState(() => new Audio());
  function play(ev: EventRow) {
    audio.pause();
    audio.src = api.eventAudioUrl(ev.event_id, "clean", "cypher");
    audio.play().catch(() => {});
  }
  return (
    <section className="card pad">
      <h3>Running Event Log</h3>
      {events.length === 0 && <p className="muted">Transmissions will appear here as they happen.</p>}
      <div className="log">
        {events.map((ev) => {
          const low = ev.transcription_confidence != null && ev.transcription_confidence < 0.8;
          return (
            <div className="logrow" key={ev.event_id}>
              <button className="event__play" onClick={() => play(ev)} title="Play clip">▶</button>
              <span className="mono muted">{ev.timestamp_start.slice(11, 19)}</span>
              <span className="mono">{ev.trainee_name}</span>
              <span className="mono">{ev.frequency}</span>
              <span title={ev.tx_mode}>{ev.tx_mode === "Cypher" ? "🔒" : "◌"}</span>
              <span className={`event__aud aud--${ev.audibility.split("-")[0].toLowerCase()}`}>{ev.audibility}</span>
              <span className={`logtext ${low ? "text--amber" : ""} ${!ev.transcription ? "text--none" : ""}`}>
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

function ScenarioTab() {
  const [atmo, setAtmo] = useState(100);
  const [crypto, setCrypto] = useState(true);
  const [jamLo, setJamLo] = useState("14.2");
  const [jamHi, setJamHi] = useState("14.3");
  const [jamOn, setJamOn] = useState(false);
  const mhz = (s: string) => (parseFloat(s) || 0) * 1e6;

  return (
    <section className="card pad">
      <h3>Scenario Controls</h3>
      <div className="field">
        <span>Atmospheric severity — {(atmo / 100).toFixed(2)}×</span>
        <input type="range" min={25} max={300} value={atmo}
          onChange={(e) => { const v = +e.target.value; setAtmo(v); api.scenario({ atmospheric_multiplier: v / 100 }); }} />
      </div>
      <label className="row gap">
        <input type="checkbox" checked={crypto} onChange={(e) => { setCrypto(e.target.checked); api.scenario({ crypto_enabled: e.target.checked }); }} />
        Crypto available to all radios
      </label>
      <div className="row gap mt">
        <span>Jamming</span>
        <input className="input mono w90" value={jamLo} onChange={(e) => setJamLo(e.target.value)} /> MHz to
        <input className="input mono w90" value={jamHi} onChange={(e) => setJamHi(e.target.value)} /> MHz
        <button className={`btn ${jamOn ? "btn--danger" : ""}`}
          onClick={() => { const on = !jamOn; setJamOn(on); api.scenario({ jamming_on: on ? [[mhz(jamLo), mhz(jamHi)]] : [] }); }}>
          {jamOn ? "Stop Jam" : "Start Jam"}
        </button>
        <button className="btn" onClick={() => api.scenario({ noise_burst: [mhz(jamLo), mhz(jamHi)] })}>Noise Burst</button>
      </div>
    </section>
  );
}

function SettingsTab({ mustChangePassword, onTimezone }: { mustChangePassword: boolean; onTimezone: (tz: string) => void }) {
  const [cfg, setCfg] = useState<Record<string, any>>({});
  const [saved, setSaved] = useState(false);
  const [pw, setPw] = useState({ current: "", next: "" });
  const [pwMsg, setPwMsg] = useState("");

  useEffect(() => { api.getConfig().then(setCfg).catch(() => {}); }, []);
  const set = (k: string, v: any) => setCfg((c) => ({ ...c, [k]: v }));

  async function save() {
    const keys = ["whisper_model", "whisper_compute_type", "transcription_confidence_threshold",
      "transcription_skip_under_seconds", "display_timezone", "crypto_delay_ms"];
    const updates: Record<string, unknown> = {};
    keys.forEach((k) => (updates[k] = cfg[k]));
    await api.updateSettings(updates);
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
        <Field label="Whisper model">
          <select className="input" value={cfg.whisper_model || "small"} onChange={(e) => set("whisper_model", e.target.value)}>
            {["tiny", "base", "small", "medium", "large-v3"].map((m) => <option key={m}>{m}</option>)}
          </select>
        </Field>
        <Field label="Compute type">
          <select className="input" value={cfg.whisper_compute_type || "int8"} onChange={(e) => set("whisper_compute_type", e.target.value)}>
            {["int8", "int8_float16", "float16"].map((m) => <option key={m}>{m}</option>)}
          </select>
        </Field>
        <Field label="Amber confidence threshold">
          <input className="input" type="number" step="0.05" min="0" max="1"
            value={cfg.transcription_confidence_threshold ?? 0.8}
            onChange={(e) => set("transcription_confidence_threshold", parseFloat(e.target.value))} />
        </Field>
        <Field label="Crypto sync delay (ms)">
          <input className="input" type="number" step="100" min="0"
            value={cfg.crypto_delay_ms ?? 1500} onChange={(e) => set("crypto_delay_ms", parseInt(e.target.value))} />
        </Field>
        <Field label="Display timezone">
          <input className="input mono" value={cfg.display_timezone || "UTC"} onChange={(e) => set("display_timezone", e.target.value)} />
        </Field>
        <button className="btn btn--primary" onClick={save}>{saved ? "Saved ✓" : "Save Settings"}</button>
      </section>

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
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
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
