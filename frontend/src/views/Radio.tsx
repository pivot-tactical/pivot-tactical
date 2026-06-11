import { useCallback, useEffect, useRef, useState } from "react";
import { ModeDial } from "../components/ModeDial";
import { SevenSegmentClock } from "../components/SevenSegmentClock";
import { AudioIO, playClick, playSyncTone } from "../audio";
import type { LoginResponse, RadioMode, TxPhase } from "../types";
import { PivotSocket } from "../ws";

// Radio view (spec §3.2.2, §7.2.2): large frequency display + tuning, a
// prominent Plain/Cypher toggle, a frequency-dependent signal indicator, the
// PTT control with the IDLE → CRYPTO SYNC → SECURE TX / TX state machine, and a
// corner seven-segment clock.

const STEP_HZ = 12_500; // tuning step / channel raster (12.5 kHz)

function snapToStep(hz: number): number {
  return Math.round(hz / STEP_HZ) * STEP_HZ;
}

function regionFor(hz: number): { label: string; signal: number } {
  // Standard ITU bands (ITU-R V.431): HF ≤30 MHz, VHF ≤300 MHz, UHF above —
  // the upper edge of each band belongs to the lower band, so 30 MHz is HF.
  const label = hz <= 30e6 ? "HF" : hz <= 300e6 ? "VHF" : "UHF";
  // The signal bar is a continuous client-side approximation of the band
  // profile (§3.2.2): propagation improves smoothly with frequency, so it is
  // log-interpolated across the tunable range rather than bucketed per band.
  const clamped = Math.max(1.6e6, Math.min(3e9, hz));
  const t =
    (Math.log10(clamped) - Math.log10(1.6e6)) /
    (Math.log10(3e9) - Math.log10(1.6e6));
  return { label, signal: 0.15 + 0.82 * t };
}

function formatMHz(hz: number): string {
  return (hz / 1e6).toFixed(4);
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
  const initialHz = login.frequency_hz ?? 7_000_000;
  const [freqHz, setFreqHz] = useState(initialHz);
  const [mode, setMode] = useState<RadioMode>(login.mode ?? "Plain");
  const [phase, setPhase] = useState<TxPhase>("IDLE");
  const [entry, setEntry] = useState(formatMHz(initialHz));
  const entryRef = useRef<HTMLInputElement>(null);
  const audio = useRef(new AudioIO());
  const region = regionFor(freqHz);
  const transmitting = phase !== "IDLE";

  // Play incoming voice; enable audio on the first user gesture (autoplay rules).
  useEffect(() => {
    socket.onAudio((buf) => audio.current.play(buf));
    const enable = () => audio.current.init().catch(() => {});
    window.addEventListener("pointerdown", enable, { once: true });
    window.addEventListener("keydown", enable, { once: true });
    return () => audio.current.close();
  }, [socket]);

  // --- WebSocket-driven state machine (§3.2.3) ---
  useEffect(() => {
    const offs = [
      socket.on("tuned", (p) => {
        setFreqHz(p.frequency_hz);
        setEntry(formatMHz(p.frequency_hz));
      }),
      socket.on("mode_changed", (p) => setMode(p.mode)),
      socket.on("ptt_started", (p) => {
        if (p.sync_applies) {
          setPhase("CRYPTO_SYNC");
          playSyncTone(); // local only — not transmitted
        } else {
          setPhase("TX");
        }
      }),
      socket.on("secure_tx", () => setPhase("SECURE_TX")),
      socket.on("ptt_ended", () => setPhase("IDLE")),
      socket.on("ptt_aborted", () => setPhase("IDLE")),
    ];
    return () => offs.forEach((off) => off && off());
  }, [socket]);

  // --- PTT ---
  const startTx = useCallback(async () => {
    if (transmitting) return;
    playClick();
    try {
      // Capture mic and stream PCM frames to the server while keyed (§6.3).
      await audio.current.startCapture((pcm) => socket.sendAudio(pcm));
    } catch {
      /* permission denied: control still proceeds; no audio reaches the net */
    }
    socket.pttStart(`${formatMHz(freqHz)} MHz`, mode);
  }, [socket, freqHz, mode, transmitting]);

  const endTx = useCallback(() => {
    playClick(700);
    audio.current.stopCapture();
    // Releasing during sync is an abort; otherwise a normal end (§3.2.3).
    if (phase === "CRYPTO_SYNC") socket.pttAbort();
    else socket.pttEnd();
    setPhase("IDLE");
  }, [socket, phase]);

  // Spacebar PTT hotkey (§3.2.2).
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code === "Space" && !e.repeat && !isTyping(e)) {
        e.preventDefault();
        startTx();
      }
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space" && !isTyping(e)) {
        e.preventDefault();
        endTx();
      }
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [startTx, endTx]);

  function applyTune(hz: number) {
    const snapped = Math.max(1.6e6, Math.min(3e9, snapToStep(hz)));
    setFreqHz(snapped);
    setEntry(formatMHz(snapped));
    socket.tune(`${formatMHz(snapped)} MHz`);
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
    const next: RadioMode = mode === "Plain" ? "Cypher" : "Plain";
    setMode(next);
    socket.modeChange(next);
  }

  return (
    <div className="radio">
      <header className="radio__top">
        <div className="radio__call mono">{login.radio_id ? "ON NET" : ""} · {region.label}</div>
        <SevenSegmentClock timezone={timezone} />
      </header>

      <div className="card radio__panel">
        <div className="freq">
          <div className="freq__display mono">{formatMHz(freqHz)}<span className="freq__unit">MHz</span></div>
          <div className="freq__controls">
            <button className="btn btn--step" aria-label="Decrease frequency" onClick={() => applyTune(freqHz - STEP_HZ)} disabled={transmitting}>
              ▼
            </button>
            <input
              ref={entryRef}
              className="input mono freq__entry"
              aria-label="Frequency in MHz"
              value={entry}
              disabled={transmitting}
              onChange={(e) => setEntry(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") confirmEntry();
              }}
            />
            <button className="btn btn--step" aria-label="Increase frequency" onClick={() => applyTune(freqHz + STEP_HZ)} disabled={transmitting}>
              ▲
            </button>
            <button className="btn btn--primary" onClick={confirmEntry} disabled={transmitting}>
              Tune
            </button>
          </div>
        </div>

        <div className="radio__row">
          <ModeDial
            mode={mode}
            onToggle={toggleMode}
            disabled={transmitting}
            title="Plain / Cypher (persists across retuning)"
          />

          <div className="signal">
            <span className="signal__label">SIGNAL</span>
            <div className="signal__bar">
              <div className="signal__fill" style={{ width: `${Math.round(region.signal * 100)}%` }} />
            </div>
          </div>
        </div>

        <button
          className={`ptt ptt--${phase.toLowerCase()}`}
          onMouseDown={startTx}
          onMouseUp={endTx}
          onMouseLeave={() => transmitting && endTx()}
          onTouchStart={(e) => {
            e.preventDefault();
            startTx();
          }}
          onTouchEnd={(e) => {
            e.preventDefault();
            endTx();
          }}
        >
          <span className="ptt__state">{phaseLabel(phase)}</span>
          <span className="ptt__hint">HOLD / SPACE</span>
        </button>
      </div>

    </div>
  );
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
