import { useCallback, useEffect, useRef, useState } from "react";
import { ModeDial } from "../components/ModeDial";
import { SevenSegmentClock } from "../components/SevenSegmentClock";
import { METER_DECAY, SignalMeter } from "../components/SignalMeter";
import { VolumeSlider } from "../components/VolumeSlider";
import {
  AudioIO,
  loadVolume,
  parseTaggedAudio,
  pcmLevel,
  playClick,
  playSyncTone,
  saveVolume,
} from "../audio";
import type { LoginResponse, RadioMode, RadioState, TxPhase } from "../types";
import { PivotSocket } from "../ws";

// Radio view (spec §3.2.2, §7.2.2): one panel per radio — large frequency
// display + tuning, a prominent Plain/Cypher toggle, a live signal meter driven
// by the received audio, the PTT control with the IDLE → CRYPTO SYNC →
// SECURE TX / TX state machine — plus a corner seven-segment clock.
//
// A terminal starts with the one radio it logged in with and can add more, the
// way the instructor console can: each extra radio tunes, keys and hears on its
// own frequency. With several nets open at once the noise floors stack up, so
// every panel also carries FOCUS — a local mute of every *other* radio, so one
// net can be read through the racket without changing what anyone else hears
// (the instructor has RX NOISE OFF for the same job).

const STEP_HZ = 12_500; // tuning step / channel raster (12.5 kHz)
const MAX_RADIOS = 9; // matches the server cap and the Shift+digit PTT hotkeys

function snapToStep(hz: number): number {
  return Math.round(hz / STEP_HZ) * STEP_HZ;
}

function regionFor(hz: number): string {
  // Standard ITU bands (ITU-R V.431): HF ≤30 MHz, VHF ≤300 MHz, UHF above —
  // the upper edge of each band belongs to the lower band, so 30 MHz is HF.
  return hz <= 30e6 ? "HF" : hz <= 300e6 ? "VHF" : "UHF";
}

function formatMHz(hz: number): string {
  return (hz / 1e6).toFixed(4);
}

// One of the terminal's radios. `slot` is the radio's number on this terminal:
// slot 1 is the radio the trainee logged in with (it goes with the terminal and
// can't be removed), and each added radio takes the next free slot. The server
// derives the radio_id from it the same way — "<trainee_id>#<slot>" — so a
// reconnect can re-declare the radios this view is still showing.
interface TraineeRadio {
  radioId: string;
  slot: number;
  name: string;
  freqHz: number;
  mode: RadioMode;
}

function slotOf(radioId: string): number {
  const tail = radioId.split("#").pop();
  const n = tail ? parseInt(tail, 10) : NaN;
  return Number.isFinite(n) && radioId.includes("#") ? n : 1;
}

function fromRadioState(r: RadioState): TraineeRadio {
  return {
    radioId: r.radio_id,
    slot: slotOf(r.radio_id),
    name: r.name,
    freqHz: r.frequency_hz,
    mode: r.mode,
  };
}

export function Radio({
  socket,
  login,
  timezone,
}: {
  socket: PivotSocket;
  login: LoginResponse;
  timezone: string;
}) {
  const primaryId = login.radio_id ?? "self";
  const [radios, setRadios] = useState<TraineeRadio[]>(() => [
    {
      radioId: primaryId,
      slot: 1,
      name: login.name ?? "RADIO",
      freqHz: login.frequency_hz ?? 7_000_000,
      mode: login.mode ?? "Plain",
    },
  ]);
  // TX phase per keyed radio (absent = IDLE). Several radios can be keyed at
  // once — the one mic feeds them all, and each runs its own PTT/crypto-sync
  // lifecycle on the server (ptt_* messages carry the radio_id).
  const [phases, setPhases] = useState<Record<string, TxPhase>>({});
  // The radio being listened to alone, if any: every other radio's playback is
  // muted while it is set.
  const [focused, setFocused] = useState<string | null>(null);
  const audio = useRef(new AudioIO());
  // Radios this terminal is currently holding keyed: gates duplicate key-downs
  // and decides when the last release stops the shared mic capture. A ref —
  // start/end fire from event handlers and must see the live set.
  const keyed = useRef<Set<string>>(new Set());
  // The live radio list for handlers that must not be re-subscribed on every
  // tune (the socket's reconnect handler, the PTT hotkeys).
  const radiosRef = useRef(radios);
  radiosRef.current = radios;

  // Live receive level per radio: topped up by each arriving PCM frame, decayed
  // by that panel's meter loop, so each bar tracks its own channel — hiss,
  // crashes, and the jump + modulation when another station transmits.
  const rxLevels = useRef<Record<string, number>>({});

  // Play incoming voice; enable audio on the first user gesture (autoplay rules).
  useEffect(() => {
    socket.onAudio((buf) => {
      // Every frame is tagged with the radio that received it, so one playback
      // stream carries all of this terminal's radios at independent volumes.
      const { radioId, pcm } = parseTaggedAudio(buf);
      rxLevels.current[radioId] = Math.max(rxLevels.current[radioId] ?? 0, pcmLevel(pcm));
      audio.current.play(pcm, radioId);
    });
    // Warm the mic (and playback) as soon as we're logged in: the login click
    // is a fresh user gesture, so the browser's mic-permission prompt appears
    // now rather than on the first PTT, and no transmission waits on
    // getUserMedia. Retry on the first in-view gesture if it was blocked (that
    // fallback also covers playback autoplay).
    const io = audio.current;
    io.prewarm().catch(() => {});
    const enable = () => io.prewarm().catch(() => {});
    window.addEventListener("pointerdown", enable, { once: true });
    window.addEventListener("keydown", enable, { once: true });
    return () => io.close();
  }, [socket]);

  // --- WebSocket-driven state machine (§3.2.3) ---
  useEffect(() => {
    // Messages about the terminal's own radio may omit the id.
    const at = (p: any): string => p?.radio_id ?? primaryId;
    const setPhase = (id: string, ph: TxPhase) => setPhases((prev) => ({ ...prev, [id]: ph }));
    const clearPhase = (id: string) =>
      setPhases((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    const patch = (id: string, fields: Partial<TraineeRadio>) =>
      setRadios((prev) => prev.map((r) => (r.radioId === id ? { ...r, ...fields } : r)));

    const offs = [
      // The extra radios live only for the length of the connection, so after a
      // dropped socket comes back they are re-declared by slot — same ids, same
      // frequencies and modes — rather than silently going deaf.
      socket.on("open", () => {
        for (const r of radiosRef.current) {
          if (r.slot > 1) socket.addRadio(r.slot, `${formatMHz(r.freqHz)} MHz`, r.mode);
        }
      }),
      socket.on("tuned", (p) => patch(at(p), { freqHz: p.frequency_hz })),
      socket.on("mode_changed", (p) => patch(at(p), { mode: p.mode })),
      socket.on("radio_added", (p: RadioState) =>
        setRadios((prev) => {
          const added = fromRadioState(p);
          const merged = prev.some((r) => r.radioId === added.radioId)
            ? prev.map((r) => (r.radioId === added.radioId ? added : r))
            : [...prev, added];
          return merged.sort((a, b) => a.slot - b.slot);
        })
      ),
      socket.on("radio_removed", (p) =>
        setRadios((prev) => prev.filter((r) => r.radioId !== p.radio_id))
      ),
      socket.on("ptt_started", (p) => {
        setPhase(at(p), p.sync_applies ? "CRYPTO_SYNC" : "TX");
        if (p.sync_applies) playSyncTone(); // local only — not transmitted
      }),
      socket.on("secure_tx", (p) => setPhase(at(p), "SECURE_TX")),
      socket.on("ptt_ended", (p) => clearPhase(at(p))),
      socket.on("ptt_aborted", (p) => clearPhase(at(p))),
    ];
    return () => offs.forEach((off) => off && off());
  }, [socket, primaryId]);

  // --- PTT ---
  const startTx = useCallback(
    async (r: TraineeRadio) => {
      if (keyed.current.has(r.radioId)) return;
      playClick();
      keyed.current.add(r.radioId);
      try {
        // Capture mic and stream PCM frames to the server while keyed (§6.3).
        await audio.current.startCapture((pcm) => socket.sendAudio(pcm));
      } catch {
        /* permission denied: control still proceeds; no audio reaches the net */
      }
      // A quick tap can release before the mic finished opening — don't key a
      // radio whose end has already been sent.
      if (!keyed.current.has(r.radioId)) return;
      socket.pttStart(`${formatMHz(r.freqHz)} MHz`, r.mode, r.radioId);
    },
    [socket]
  );

  const endTx = useCallback(
    (r: TraineeRadio) => {
      if (!keyed.current.has(r.radioId)) return;
      keyed.current.delete(r.radioId);
      playClick(700);
      if (keyed.current.size === 0) audio.current.stopCapture();
      // Releasing during sync is an abort; otherwise a normal end (§3.2.3).
      if (phases[r.radioId] === "CRYPTO_SYNC") socket.pttAbort(r.radioId);
      else socket.pttEnd(r.radioId);
      setPhases((prev) => {
        const next = { ...prev };
        delete next[r.radioId];
        return next;
      });
    },
    [socket, phases]
  );

  // PTT hotkeys (§3.2.2): the spacebar keys the focused radio — the one being
  // listened to — falling back to the terminal's own radio, and Shift + a
  // radio's number keys that one, so any open net can be answered by hand.
  useEffect(() => {
    const target = (): TraineeRadio =>
      radiosRef.current.find((r) => r.radioId === focused) ?? radiosRef.current[0];
    const numbered = (e: KeyboardEvent): TraineeRadio | undefined => {
      const m = e.code.match(/^Digit([1-9])$/);
      return m ? radiosRef.current[parseInt(m[1], 10) - 1] : undefined;
    };
    const down = (e: KeyboardEvent) => {
      if (isTyping(e) || e.repeat) return;
      if (e.shiftKey) {
        const r = numbered(e);
        if (r) {
          e.preventDefault();
          startTx(r);
        }
        return;
      }
      if (e.code === "Space") {
        e.preventDefault();
        startTx(target());
      }
    };
    const up = (e: KeyboardEvent) => {
      if (isTyping(e)) return;
      // Shift may already be released by the time the digit comes up.
      const r = numbered(e);
      if (r) {
        e.preventDefault();
        endTx(r);
        return;
      }
      if (e.code === "Space") {
        e.preventDefault();
        endTx(target());
      }
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [startTx, endTx, focused]);

  // --- radio set ---
  function addRadio() {
    const used = new Set(radios.map((r) => r.slot));
    let slot = 2;
    while (used.has(slot)) slot++;
    if (slot > MAX_RADIOS) return;
    // The server answers with radio_added, which carries the authoritative id,
    // label and starting frequency.
    socket.addRadio(slot);
  }

  function removeRadio(r: TraineeRadio) {
    if (r.slot === 1) return; // the terminal's own radio goes with the terminal
    socket.removeRadio(r.radioId);
    // Local filter for snappiness; the server's radio_removed follows.
    setRadios((prev) => prev.filter((x) => x.radioId !== r.radioId));
    setFocused((f) => (f === r.radioId ? null : f));
    delete rxLevels.current[r.radioId];
  }

  function retune(radioId: string, freqHz: number) {
    setRadios((prev) => prev.map((r) => (r.radioId === radioId ? { ...r, freqHz } : r)));
  }

  function changeMode(radioId: string, mode: RadioMode) {
    setRadios((prev) => prev.map((r) => (r.radioId === radioId ? { ...r, mode } : r)));
  }

  return (
    <div className={`radio ${radios.length > 1 ? "radio--multi" : ""}`}>
      <header className="radio__top">
        <div className="radio__call mono">{login.radio_id ? "ON NET" : ""}</div>
        <SevenSegmentClock timezone={timezone} />
      </header>

      <div className="radio__panels">
        {radios.map((r, i) => (
          <RadioPanel
            key={r.radioId}
            radio={r}
            index={i + 1}
            multi={radios.length > 1}
            phase={phases[r.radioId] ?? "IDLE"}
            focused={focused === r.radioId}
            muted={focused !== null && focused !== r.radioId}
            spaceTarget={focused === r.radioId || (focused === null && i === 0)}
            socket={socket}
            audio={audio.current}
            rxLevels={rxLevels}
            onTune={retune}
            onMode={changeMode}
            onStart={startTx}
            onEnd={endTx}
            onFocus={() => setFocused((f) => (f === r.radioId ? null : r.radioId))}
            onRemove={removeRadio}
          />
        ))}
      </div>

      {radios.length < MAX_RADIOS && (
        <button className="radio__add" onClick={addRadio}>
          + Add Radio
        </button>
      )}
    </div>
  );
}

// The per-radio receive levels live in a ref shared with the socket's audio
// handler; the meters poll and decay it at animation rate without re-renders.
type RxLevels = { current: Record<string, number> };

function RadioPanel({
  radio,
  index,
  multi,
  phase,
  focused,
  muted,
  spaceTarget,
  socket,
  audio,
  rxLevels,
  onTune,
  onMode,
  onStart,
  onEnd,
  onFocus,
  onRemove,
}: {
  radio: TraineeRadio;
  index: number;
  multi: boolean;
  phase: TxPhase;
  focused: boolean;
  muted: boolean;
  spaceTarget: boolean;
  socket: PivotSocket;
  audio: AudioIO;
  rxLevels: RxLevels;
  onTune: (radioId: string, freqHz: number) => void;
  onMode: (radioId: string, mode: RadioMode) => void;
  onStart: (r: TraineeRadio) => void;
  onEnd: (r: TraineeRadio) => void;
  onFocus: () => void;
  onRemove: (r: TraineeRadio) => void;
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
    const snapped = Math.max(1.6e6, Math.min(3e9, snapToStep(hz)));
    onTune(radio.radioId, snapped);
    socket.tune(`${formatMHz(snapped)} MHz`, radio.radioId);
  }

  // Confirm the typed frequency and hand focus back to the page — otherwise it
  // stays in the entry box and the spacebar PTT (§3.4.5) just types spaces
  // into it instead of keying up.
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
              disabled={transmitting}
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
            onClick={() => applyTune(radio.freqHz - STEP_HZ)}
            disabled={transmitting}
          >
            ▼
          </button>
          <input
            ref={entryRef}
            className="input mono freq__entry"
            aria-label={`Frequency in MHz on ${radio.name}`}
            value={entry}
            disabled={transmitting}
            onChange={(e) => setEntry(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") confirmEntry();
            }}
          />
          <button
            className="btn btn--step"
            aria-label={`Increase frequency on ${radio.name}`}
            onClick={() => applyTune(radio.freqHz + STEP_HZ)}
            disabled={transmitting}
          >
            ▲
          </button>
          <button
            className="btn btn--primary"
            aria-label={`Tune ${radio.name}`}
            onClick={confirmEntry}
            disabled={transmitting}
          >
            Tune
          </button>
        </div>
      </div>

      <div className="radio__row">
        <ModeDial
          mode={radio.mode}
          onToggle={toggleMode}
          disabled={transmitting}
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

      <button
        className={`ptt ptt--${phase.toLowerCase()}`}
        aria-label={`Push to talk on ${radio.name}`}
        onMouseDown={() => onStart(radio)}
        onMouseUp={() => onEnd(radio)}
        onMouseLeave={() => transmitting && onEnd(radio)}
        onTouchStart={(e) => {
          e.preventDefault();
          onStart(radio);
        }}
        onTouchEnd={(e) => {
          e.preventDefault();
          onEnd(radio);
        }}
      >
        <span className="ptt__state">{phaseLabel(phase)}</span>
        <span className="ptt__hint">{hotkeyHint(multi, index, spaceTarget)}</span>
      </button>
    </div>
  );
}

// What keys this radio: the spacebar follows the focused radio (the one being
// listened to), and every radio within reach of the number row shows its own
// Shift + digit combo so there is no ambiguity about which one keys up.
function hotkeyHint(multi: boolean, index: number, spaceTarget: boolean): string {
  if (!multi) return "HOLD / SPACE";
  const keys = [spaceTarget ? "SPACE" : null, index <= 9 ? `SHIFT+${index}` : null].filter(Boolean);
  return keys.length ? `HOLD · ${keys.join(" · ")}` : "HOLD";
}

function phaseLabel(phase: TxPhase): string {
  switch (phase) {
    case "CRYPTO_SYNC":
      return "CRYPTO SYNC…";
    case "SECURE_TX":
      return "SECURE TX";
    case "TX":
      return "TX";
    default:
      return "PUSH TO TALK";
  }
}

function isTyping(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement;
  return el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA");
}
