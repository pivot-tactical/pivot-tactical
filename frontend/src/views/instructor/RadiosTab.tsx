import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { AudioIO, loadVolume, saveVolume } from "../../audio";
import { ModeDial } from "../../components/ModeDial";
import { METER_DECAY, SignalMeter } from "../../components/SignalMeter";
import { VolumeSlider } from "../../components/VolumeSlider";
import { formatMHz, snapToGrid, steppedFrom } from "../../freq";
import type { EventRow, LogEntry, NetScenario, RadioState, TxPhase } from "../../types";
import { PivotSocket } from "../../ws";
import { LiveLogTab } from "./LiveLogTab";

const fmtMHz = formatMHz;

export function scenarioFor(netScenarios: NetScenario[], hz: number): NetScenario | undefined {
  return netScenarios.find((s) => s.frequency_hz === hz);
}

export type RxLevels = { current: Record<string, number> };

export function updateRadio(radios: RadioState[], r: RadioState): RadioState[] {
  return radios.map((x) => (x.radio_id === r.radio_id ? r : x));
}

export function phaseLabel(p: TxPhase) {
  return p === "CRYPTO_SYNC" ? "CRYPTO SYNC…" : p === "SECURE_TX" ? "SECURE TX" : p === "TX" ? "TX" : "PUSH TO TALK";
}

export function typing(e: KeyboardEvent) {
  const el = e.target as HTMLElement;
  return el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT");
}

export function RadiosTab({ radios, socket, audio, onChange, entries, timezone, netScenarios, rxLevels, onEventUpdate }: {
  radios: RadioState[]; socket: PivotSocket | null; audio: AudioIO;
  onChange: (rs: RadioState[]) => void; entries: LogEntry[]; timezone: string;
  netScenarios: NetScenario[]; rxLevels: RxLevels;
  onEventUpdate: (ev: Partial<EventRow> & { event_id: string }) => void;
}) {
  const [phases, setPhases] = useState<Record<string, TxPhase>>({});
  const keyed = useRef<Set<string>>(new Set());

  useEffect(() => {
    const setPhase = (id: string, ph: TxPhase) =>
      setPhases((prev) => (prev[id] === ph ? prev : { ...prev, [id]: ph }));
    const clearPhase = (id: string) =>
      setPhases((prev) => {
        if (!(id in prev)) return prev;
        const next = { ...prev };
        delete next[id];
        return next;
      });

    const offs = [
      socket?.on("instr_ptt_start", ({ radio_id, phase }) => setPhase(radio_id, phase ?? "TX")),
      socket?.on("instr_ptt_phase", ({ radio_id, phase }) => setPhase(radio_id, phase)),
      socket?.on("instr_ptt_end", ({ radio_id }) => clearPhase(radio_id)),
      socket?.on("instr_ptt_abort", ({ radio_id }) => clearPhase(radio_id)),
    ];
    return () => offs.forEach((off) => off?.());
  }, [socket]);

  const startTx = useCallback(async (r: RadioState) => {
    if (!socket) return;
    if (audio) {
      await audio.prewarm().catch(() => {});
    }
    socket.instrPttStart(r.radio_id, `${fmtMHz(r.frequency_hz)} MHz`, r.mode);
  }, [socket, audio]);

  const endTx = useCallback((r: RadioState) => {
    socket?.instrPttEnd(r.radio_id);
  }, [socket]);

  useEffect(() => {
    const numbered = (e: KeyboardEvent): RadioState | undefined => {
      const m = e.code.match(/^Numpad([1-9])$/);
      if (!m) return undefined;
      const idx = parseInt(m[1], 10) - 1;
      return radios[idx];
    };

    const down = (e: KeyboardEvent) => {
      if (e.repeat || typing(e)) return;
      const r = numbered(e);
      if (!r || keyed.current.has(r.radio_id)) return;
      e.preventDefault();
      keyed.current.add(r.radio_id);
      startTx(r);
    };

    const up = (e: KeyboardEvent) => {
      const r = numbered(e);
      if (!r || !keyed.current.has(r.radio_id)) return;
      e.preventDefault();
      keyed.current.delete(r.radio_id);
      endTx(r);
    };

    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, [radios, startTx, endTx]);

  async function addRadio() {
    // Omit the frequency so the server applies the operator-configured
    // default start frequency (Settings → Default start frequency).
    const r = await api.addInstructorRadio();
    onChange([...radios, r]);
  }

  async function removeRadio(id: string) {
    if (!window.confirm("Are you sure you want to remove this radio?")) return;
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
      <LiveLogTab entries={entries} timezone={timezone} onEventUpdate={onEventUpdate} />
    </div>
  );
}

function InstrRadioCard({ radio, index, socket, audio, phase, scenario, rxLevels, onStart, onEnd, onRemove }: {
  radio: RadioState; index: number; socket: PivotSocket | null; audio: AudioIO; phase: TxPhase;
  scenario: NetScenario | undefined; rxLevels: RxLevels;
  onStart: (r: RadioState) => void; onEnd: (r: RadioState) => void; onRemove: (id: string) => void;
}) {
  const [entry, setEntry] = useState(fmtMHz(radio.frequency_hz));
  const [volume, setVolume] = useState(() => loadVolume(`instr.${radio.radio_id}`));
  const entryRef = useRef<HTMLInputElement>(null);
  const transmitting = phase !== "IDLE";
  const shortcut = index <= 9 ? `NUMPAD ${index}` : null;

  const interference = scenario?.interference ?? 0;
  const jammed = scenario?.jammed ?? false;
  const rxNoiseOn = radio.rx_noise !== false;

  const readRxLevel = useCallback(
    () => (rxLevels.current[radio.radio_id] = (rxLevels.current[radio.radio_id] ?? 0) * METER_DECAY),
    [rxLevels, radio.radio_id],
  );

  const [intPct, setIntPct] = useState(Math.round(interference * 100));
  useEffect(() => {
    setIntPct(Math.round(interference * 100));
  }, [interference, radio.frequency_hz]);

  function setNet(patch: { interference?: number; jammed?: boolean }) {
    api.scenario({ net_scenario: { frequency_hz: radio.frequency_hz, ...patch } }).catch(() => {});
  }

  useEffect(() => { setEntry(fmtMHz(radio.frequency_hz)); }, [radio.frequency_hz]);

  useEffect(() => { audio.setVolume(volume, radio.radio_id); }, [audio, radio.radio_id, volume]);
  function changeVolume(v: number) {
    setVolume(v);
    saveVolume(`instr.${radio.radio_id}`, v);
  }

  function tuneTo(hz: number) {
    const snapped = snapToGrid(hz);
    socket?.instrTune(radio.radio_id, `${fmtMHz(snapped)} MHz`);
  }

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
          <button className="btn btn--ghost instr-radio__remove" aria-label={`Remove radio ${radio.name}`} title={`Remove radio ${radio.name}`}
            onClick={() => onRemove(radio.radio_id)} disabled={transmitting}>✕</button>
        </div>

        <div className="freq">
          <div className="freq__display mono">{fmtMHz(radio.frequency_hz)}<span className="freq__unit">MHz</span></div>
          <div className="freq__controls">
            <button className="btn btn--step" aria-label={`Decrease frequency on ${radio.name}`}
              onClick={() => tuneTo(steppedFrom(radio.frequency_hz, -1))} disabled={transmitting}>▼</button>
            <input ref={entryRef} className="input mono freq__entry" aria-label={`Frequency in MHz on ${radio.name}`}
              value={entry} disabled={transmitting}
              onChange={(e) => setEntry(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") confirmEntry(); }} />
            <button className="btn btn--step" aria-label={`Increase frequency on ${radio.name}`}
              onClick={() => tuneTo(steppedFrom(radio.frequency_hz, 1))} disabled={transmitting}>▲</button>
            <button className="btn btn--primary" aria-label={`Tune ${radio.name}`} onClick={confirmEntry} disabled={transmitting}>Tune</button>
          </div>
        </div>

        <div className="radio__row">
          <ModeDial
            mode={radio.mode}
            onToggle={() => socket?.instrMode(radio.radio_id, radio.mode === "Cypher" ? "Plain" : "Cypher")}
            disabled={transmitting}
            title={`Plain / Cypher on ${radio.name} (persists across retuning)`}
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
              aria-label={`Noise offset on ${radio.name} channel (0 = natural baseline)`}
              title={`Noise on ${radio.name} channel: 0 is the frequency's natural baseline; raise it to induce interference, lower it to temporarily clean the channel up`}
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
              aria-label={`Jam ${radio.name} channel`}
              title={`Jam ${radio.name} channel (a wall of jammer noise; trainees must change frequency)`}
              onClick={() => setNet({ jammed: !jammed })}
            >
              {jammed ? "JAMMING" : "Jam"}
            </button>
          </div>
        </div>

        <div className="rxctl">
          <VolumeSlider value={volume} onChange={changeVolume} ariaLabel={`Headset volume for ${radio.name}`} />
          <button
            className={`btn ${rxNoiseOn ? "" : "btn--warn"}`}
            aria-label={`Toggle RX Noise on ${radio.name}`}
            title={`Channel noise on ${radio.name}'s receive only — turn it off to monitor this net unhindered. Every other station still hears the channel noise (shape the net itself with the CHANNEL NOISE controls).`}
            onClick={() => socket?.instrRxNoise(radio.radio_id, !rxNoiseOn)}
          >
            {rxNoiseOn ? "RX Noise: On" : "RX NOISE OFF"}
          </button>
        </div>
      </div>

      <button
        className={`ptt ptt--${phase.toLowerCase()}`}
        aria-label={`Push to talk on ${radio.name}`}
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
