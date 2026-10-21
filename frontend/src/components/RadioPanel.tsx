import { useCallback, useEffect, useRef, useState } from "react";
import { PttButton } from "./PttButton";
import { ModeDial } from "./ModeDial";
import { METER_DECAY, SignalMeter } from "./SignalMeter";
import { VolumeSlider } from "./VolumeSlider";
import { AudioIO, loadVolume, saveVolume } from "../audio";
import { formatMHz, regionFor, snapToGrid, steppedFrom } from "../freq";
import type { RadioMode, TxPhase } from "../types";
import { PivotSocket } from "../ws";

export interface TraineeRadio {
  radioId: string;
  slot: number;
  name: string;
  freqHz: number;
  mode: RadioMode;
}

// The per-radio receive levels live in a ref shared with the socket's audio
// handler; the meters poll and decay it at animation rate without re-renders.
export type RxLevels = { current: Record<string, number> };

export function RadioPanel({
  radio,
  index,
  multi,
  phase,
  focused,
  muted,
  socket,
  audio,
  rxLevels,
  onTune,
  onMode,
  onStart,
  onEnd,
  onFocus,
  onRemove,
  controlsDisabled = false,
  pttDisabled = false,
  shortcut,
}: {
  radio: TraineeRadio;
  index: number;
  multi: boolean;
  phase: TxPhase;
  focused: boolean;
  muted: boolean;
  socket: PivotSocket;
  audio: AudioIO;
  rxLevels: RxLevels;
  onTune: (radioId: string, freqHz: number) => void;
  onMode: (radioId: string, mode: RadioMode) => void;
  onStart: (r: TraineeRadio) => void;
  onEnd: (r: TraineeRadio) => void;
  onFocus: () => void;
  onRemove: (r: TraineeRadio) => void;
  controlsDisabled?: boolean;
  pttDisabled?: boolean;
  shortcut?: string;
}) {
  const [entry, setEntry] = useState(formatMHz(radio.freqHz));
  // Each radio keeps its own headset volume across refreshes; the terminal's
  // own radio keeps the key it has always used.
  const volKey = radio.slot === 1 ? "trainee" : `trainee.${radio.slot}`;
  const [volume, setVolume] = useState(() => loadVolume(volKey));
  const entryRef = useRef<HTMLInputElement>(null);
  const transmitting = phase !== "IDLE";
  const region = regionFor(radio.freqHz);

  // This radio's live receive level (shared map, see Radio): the meter shows
  // what this channel actually sounds like, whether or not it is muted.
  const readRxLevel = useCallback(
    () => (rxLevels.current[radio.radioId] = (rxLevels.current[radio.radioId] ?? 0) * METER_DECAY),
    [rxLevels, radio.radioId]
  );

  // Apply the saved headset volume to this radio's playback — silenced outright
  // while another radio holds focus, so only the net being read is audible.
  useEffect(() => {
    audio.setVolume(muted ? 0 : volume, radio.radioId);
  }, [audio, muted, volume, radio.radioId]);

  const changeVolume = useCallback(
    (v: number) => {
      setVolume(v);
      saveVolume(volKey, v);
    },
    [volKey]
  );

  // Keep the entry box in step with server-confirmed tunes without clobbering
  // what the operator is typing mid-edit.
  useEffect(() => {
    setEntry(formatMHz(radio.freqHz));
  }, [radio.freqHz]);

  function applyTune(hz: number) {
    const snapped = snapToGrid(hz);
    onTune(radio.radioId, snapped);
    socket.tune(`${formatMHz(snapped)} MHz`, radio.radioId);
  }

  // Confirm the typed frequency and hand focus back to the page — otherwise it
  // stays in the entry box and the numpad PTT (§3.4.5) just types digits into
  // it instead of keying up.
  function confirmEntry() {
    const v = parseFloat(entry);
    if (!isNaN(v)) applyTune(v * 1e6);
    entryRef.current?.blur();
  }

  function toggleMode() {
    if (transmitting) return; // disabled during own TX (§3.4.5)
    const next: RadioMode = radio.mode === "Plain" ? "Cypher" : "Plain";
    onMode(radio.radioId, next);
    socket.modeChange(next, radio.radioId);
  }

  return (
    <div className={`card radio__panel ${muted ? "radio__panel--muted" : ""}`}>
      {multi && (
        <div className="radio__panelhead">
          <span className="radio__num mono" aria-hidden>
            {index}
          </span>
          <span className="radio__name mono">{radio.name}</span>
          {muted && <span className="radio__mutetag mono">MUTED</span>}
          {radio.slot > 1 && (
            <button
              className="btn btn--ghost radio__remove"
              aria-label={`Remove ${radio.name}`}
              title={`Remove ${radio.name}`}
              onClick={() => onRemove(radio)}
              disabled={transmitting || controlsDisabled}
            >
              ✕
            </button>
          )}
        </div>
      )}

      <div className="freq">
        <div className="freq__display mono">
          {formatMHz(radio.freqHz)}
          <span className="freq__unit">MHz</span>
        </div>
        <div className="freq__controls">
          <button
            className="btn btn--step"
            aria-label={`Decrease frequency on ${radio.name}`}
            onClick={() => applyTune(steppedFrom(radio.freqHz, -1))}
            disabled={transmitting || controlsDisabled}
          >
            ▼
          </button>
          <input
            ref={entryRef}
            className="input mono freq__entry"
            aria-label={`Frequency in MHz on ${radio.name}`}
            value={entry}
            disabled={transmitting || controlsDisabled}
            onChange={(e) => setEntry(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") confirmEntry();
            }}
          />
          <button
            className="btn btn--step"
            aria-label={`Increase frequency on ${radio.name}`}
            onClick={() => applyTune(steppedFrom(radio.freqHz, 1))}
            disabled={transmitting || controlsDisabled}
          >
            ▲
          </button>
          <button
            className="btn btn--primary"
            aria-label={`Tune ${radio.name}`}
            onClick={confirmEntry}
            disabled={transmitting || controlsDisabled}
          >
            Tune
          </button>
        </div>
      </div>

      <div className="radio__row">
        <ModeDial
          mode={radio.mode}
          onToggle={toggleMode}
          disabled={transmitting || controlsDisabled}
          title={`Plain / Cypher on ${radio.name} (persists across retuning)`}
        />

        <SignalMeter label={`SIGNAL · ${region}`} read={readRxLevel} />
      </div>

      <div className="rxctl">
        <VolumeSlider
          value={volume}
          onChange={changeVolume}
          ariaLabel={`Headset volume for ${radio.name}`}
        />
        <button
          className={`btn ${focused ? "btn--focus" : ""}`}
          aria-label={`Focus ${radio.name}`}
          aria-pressed={focused}
          title={
            focused
              ? `Listening to ${radio.name} alone — click to bring the other radios back`
              : `Listen to ${radio.name} alone: mutes every other radio (and its noise) until you click again`
          }
          onClick={onFocus}
        >
          {focused ? "FOCUSED" : "Focus"}
        </button>
      </div>

      <PttButton
        phase={phase}
        disabled={pttDisabled}
        label={`Push to talk on ${radio.name}`}
        hint={`HOLD · ${shortcut ?? `NUMPAD ${index}`}`}
        onStart={() => onStart(radio)}
        onEnd={() => onEnd(radio)}
      />
    </div>
  );
}
