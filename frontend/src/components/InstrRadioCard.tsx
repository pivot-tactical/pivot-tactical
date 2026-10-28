import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { AudioIO, loadVolume, saveVolume } from "../audio";
import { ModeDial } from "./ModeDial";
import { METER_DECAY, SignalMeter } from "./SignalMeter";
import { VolumeSlider } from "./VolumeSlider";
import { formatMHz as fmtMHz, snapToGrid, steppedFrom } from "../freq";
import type { NetScenario, RadioState, TxPhase } from "../types";
import { PivotSocket } from "../ws";

export type RxLevels = React.MutableRefObject<Record<string, number>>;

function phaseLabel(p: TxPhase) {
  return p === "CRYPTO_SYNC" ? "CRYPTO SYNC…" : p === "SECURE_TX" ? "SECURE TX" : p === "TX" ? "TX" : "PUSH TO TALK";
}

export interface InstrRadioCardProps {
  radio: RadioState;
  index: number;
  socket: PivotSocket | null;
  audio: AudioIO;
  phase: TxPhase;
  scenario: NetScenario | undefined;
  rxLevels: RxLevels;
  onStart: (r: RadioState) => void;
  onEnd: (r: RadioState) => void;
  onRemove: (id: string) => void;
}

// One instructor radio rendered like the trainee panel: large frequency display
// + tuning, the Plain/Cypher dial, a signal indicator, its own PTT keyed by the
// numpad key with the card's number (shown on the control so there is no doubt),
// and the channel-effects controls — per-net interference and jamming applied
// to whatever frequency this radio is tuned to (§3.1.5).
export function InstrRadioCard({ radio, index, socket, audio, phase, scenario, rxLevels, onStart, onEnd, onRemove }: InstrRadioCardProps) {
  const [entry, setEntry] = useState(fmtMHz(radio.frequency_hz));
  const [volume, setVolume] = useState(() => loadVolume(`instr.${radio.radio_id}`));
  const entryRef = useRef<HTMLInputElement>(null);
  const transmitting = phase !== "IDLE";
  const shortcut = index <= 9 ? `NUMPAD ${index}` : null;

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
    const snapped = snapToGrid(hz);
    socket?.instrTune(radio.radio_id, `${fmtMHz(snapped)} MHz`);
  }
  // Confirm a typed frequency and hand focus back so the numpad PTT keys up
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
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            if (!e.repeat) onStart(radio);
          }
        }}
        onKeyUp={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onEnd(radio);
          }
        }}
      >
        <span className="ptt__state">{phaseLabel(phase)}</span>
        <span className="ptt__hint">{shortcut ? `HOLD · ${shortcut}` : "HOLD"}</span>
      </button>
    </section>
  );
}
