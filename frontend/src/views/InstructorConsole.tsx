import { useCallback, useEffect, useRef, useState } from "react";
import { api, getToken } from "../api";
import type { UpdateStatus } from "../api";
import { AudioIO, playClick, playSyncTone } from "../audio";
import { ConnectionBanner } from "../components/ConnectionBanner";
import type { ConnState } from "../components/ConnectionBanner";
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
  const [conn, setConn] = useState<ConnState>("online");
  const restartingRef = useRef(false);
  const restartPollRef = useRef<number | undefined>(undefined);
  const socketRef = useRef<PivotSocket | null>(null);
  const audio = useRef(new AudioIO());

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
    const sock = new PivotSocket({ token: getToken() || "" });
    sock.on("open", () => setConn("online"));
    sock.on("close", () => {
      if (restartingRef.current) { setConn("restarting"); startRestartPoll(); }
      else setConn("offline");
    });
    sock.on("instructor_radios", (p) => setRadios(p));
    sock.on("terminal_update", (p) => setTerminals((p.terminals || []).filter((t: Terminal) => !t.is_instructor)));
    sock.on("event_logged", (ev) => setEvents((prev) => [ev, ...prev].slice(0, 200)));
    sock.on("transcription_updated", (ev) =>
      setEvents((prev) => prev.map((e) => (e.event_id === ev.event_id ? { ...e, ...ev } : e)))
    );
    sock.on("session_started", () => setSessionActive(true));
    sock.on("session_ended", () => setSessionActive(false));
    sock.onAudio((buf) => audio.current.play(buf)); // hear trainees on instructor radios
    sock.connect();
    socketRef.current = sock;

    // Enable audio on the first user gesture (autoplay rules).
    const io = audio.current;
    const enable = () => io.init().catch(() => {});
    window.addEventListener("pointerdown", enable, { once: true });
    window.addEventListener("keydown", enable, { once: true });

    api.instructorRadios().then(setRadios).catch(() => {});
    api.terminals().then((t) => {
      setSessionActive(t.session_active);
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
        {(["radios", "log", "monitor", "scenario", "settings"] as Tab[]).map((t) => (
          <button key={t} className={`tabbtn ${tab === t ? "tabbtn--on" : ""}`} onClick={() => setTab(t)}>
            {t === "log" ? "Live Log" : t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </nav>

      <main className="console__body">
        {tab === "radios" && <RadiosTab radios={radios} socket={socketRef.current} audio={audio.current} onChange={setRadios} />}
        {tab === "log" && <LiveLogTab events={events} />}
        {tab === "monitor" && <MonitorTab terminals={terminals} />}
        {tab === "scenario" && <ScenarioTab />}
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
function snapMHzInput(mhzStr: string, fallback = 30): number {
  const hz = (parseFloat(mhzStr) || fallback) * 1e6;
  return snapToStep(hz);
}

function RadiosTab({ radios, socket, audio, onChange }: {
  radios: RadioState[]; socket: PivotSocket | null; audio: AudioIO; onChange: (r: RadioState[]) => void;
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

  const startTx = useCallback(async () => {
    if (!socket || !active || phase !== "IDLE") return;
    playClick();
    try {
      await audio.startCapture((pcm) => socket.sendAudio(pcm));
    } catch {
      /* mic blocked: control proceeds, no audio reaches the net */
    }
    socket.instrPttStart(active.radio_id, active.frequency, active.mode);
  }, [socket, active, phase, audio]);

  const endTx = useCallback(() => {
    if (!socket || !active) return;
    playClick(700);
    audio.stopCapture();
    if (phase === "CRYPTO_SYNC") socket.instrPttAbort(active.radio_id);
    else socket.instrPttEnd(active.radio_id);
    setPhase("IDLE");
  }, [socket, active, phase, audio]);

  useEffect(() => {
    const down = (e: KeyboardEvent) => { if (e.code === "Space" && !e.repeat && !typing(e)) { e.preventDefault(); startTx(); } };
    const up = (e: KeyboardEvent) => { if (e.code === "Space" && !typing(e)) { e.preventDefault(); endTx(); } };
    window.addEventListener("keydown", down); window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, [startTx, endTx]);

  async function addRadio() {
    const snapped = snapMHzInput(newFreq, 30);
    setNewFreq(fmtMHz(snapped));
    const r = await api.addInstructorRadio(`${fmtMHz(snapped)} MHz`);
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
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        const el = e.target as HTMLInputElement;
                        const snapped = snapMHzInput(el.value, r.frequency_hz / 1e6);
                        el.value = fmtMHz(snapped);
                        socket?.instrTune(r.radio_id, `${fmtMHz(snapped)} MHz`);
                      }
                    }} />
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

function SettingsTab({ mustChangePassword, onTimezone, socket, onRestart, sessionActive }: {
  mustChangePassword: boolean; onTimezone: (tz: string) => void; socket: PivotSocket | null;
  onRestart: () => void; sessionActive: boolean;
}) {
  const [cfg, setCfg] = useState<Record<string, any>>({});
  const [saved, setSaved] = useState(false);
  const [pw, setPw] = useState({ current: "", next: "" });
  const [pwMsg, setPwMsg] = useState("");
  const [upd, setUpd] = useState<UpdateStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [applying, setApplying] = useState<string | null>(null);
  const [staged, setStaged] = useState<string | null>(null);
  const [applyErr, setApplyErr] = useState<string | null>(null);
  const [restartErr, setRestartErr] = useState<string | null>(null);

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
    if (result.auto_staged) setStaged(result.auto_staged);
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

  async function applyUpdate(a: { tag: string; asset_url: string; sha256_url: string; asset_name: string }) {
    setApplying(a.tag);
    setApplyErr(null);
    try {
      const res = await api.applyUpdate(a.tag, a.asset_url, a.sha256_url, a.asset_name);
      // WinSparkle takes over on the server machine (its own dialog + restart);
      // the staged path needs a manual restart to finish.
      setStaged(res.handed_to === "winsparkle" ? `${a.tag}:winsparkle` : a.tag);
    } catch (e: any) {
      setApplyErr(e?.message ?? "Download failed");
    } finally {
      setApplying(null);
    }
  }

  useEffect(() => { api.getConfig().then(setCfg).catch(() => {}); }, []);
  const set = (k: string, v: any) => setCfg((c) => ({ ...c, [k]: v }));

  async function save() {
    const keys = ["whisper_model", "whisper_compute_type", "transcription_confidence_threshold",
      "transcription_skip_under_seconds", "display_timezone", "crypto_delay_ms",
      "update_channel", "auto_update", "update_check_on_startup"];
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
          const stagedTag = staged || (upd.auto_staged ?? null);
          const stagedViaWS = staged?.endsWith(":winsparkle");
          if (stagedTag) {
            const tag = stagedTag.replace(":winsparkle", "");
            return (
              <p className="mt" style={{ fontWeight: 600 }}>
                {stagedViaWS
                  ? `Installing ${tag} — the server will restart ✓`
                  : `${tag} ready — restart PIVOT to apply ✓`}
              </p>
            );
          }
          if (upd.auto_state === "downloading")
            return <p className="muted mt">⟳ {upd.auto_message || "Downloading update…"}</p>;
          if (upd.auto_state === "deferred_session_active")
            return <p className="mt" style={{ fontWeight: 600 }}>{upd.auto_message || "Update deferred until the session ends."}</p>;
          if (upd.auto_state === "error")
            return <p className="login__hint mt">Auto-update failed: {upd.auto_message}</p>;
          if (!upd.reachable && !upd.checking)
            return <p className="login__hint mt">GitHub unreachable — connect to the internet, or use offline import.</p>;
          if (upd.reachable && upd.available.length === 0)
            return <p className="mt" style={{ fontWeight: 600 }}>You’re up to date.</p>;
          if (upd.reachable && upd.available.length > 0)
            return <p className="mt" style={{ fontWeight: 600 }}>
              {upd.available.length} newer release{upd.available.length > 1 ? "s" : ""} available
              {upd.auto_update ? " — will install automatically when no session is running." : ":"}
            </p>;
          return null;
        })()}

        {/* Per-release rows: only shown when there is something to install and
            nothing is already staged (the staged headline covers that case). */}
        {upd && !staged && !upd.auto_staged && upd.reachable && upd.available.map((a) => (
          <div className="row between mt" key={a.tag}>
            <span className="mono">{a.tag}{a.prerelease ? " · prerelease" : ""}</span>
            {applying === a.tag ? (
              <span className="muted">{upd.updater === "winsparkle" ? "Starting installer…" : "Downloading…"}</span>
            ) : a.has_asset ? (
              <button className="btn btn--primary" onClick={() => applyUpdate(a)}
                disabled={applying !== null}>
                {upd.updater === "winsparkle" ? "Install & restart" : "Download & install"}
              </button>
            ) : (
              <span className="muted">No build for this platform</span>
            )}
          </div>
        ))}
        {applyErr && <p className="login__hint mt">{applyErr}</p>}

        {/* Restart from the browser: applies a staged update on the way back up,
            and is useful on its own. WinSparkle drives its own restart, so the
            button is hidden in that mode. */}
        {upd?.updater !== "winsparkle" && (() => {
          const hasStaged = !!(staged || upd?.auto_staged);
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
          {upd?.updater === "winsparkle"
            ? "WinSparkle verifies the signed installer, applies it and restarts PIVOT — out-of-band, never mid-session."
            : "Updates are downloaded and staged, then applied on the next restart — out-of-band, never mid-session. Air-gapped sites can use offline import."}
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
