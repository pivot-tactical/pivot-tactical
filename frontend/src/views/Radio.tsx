import { useCallback, useEffect, useRef, useState } from "react";
import { SevenSegmentClock } from "../components/SevenSegmentClock";
import { MicCapture, playClick, playSyncTone } from "../audio";
import type { LoginResponse, RadioMode, TxPhase } from "../types";
import { PivotSocket } from "../ws";

// Radio view (spec §3.2.2, §7.2.2): large frequency display + tuning, a
// prominent Plain/Cypher toggle, a frequency-dependent signal indicator, the
// PTT control with the IDLE → CRYPTO SYNC → SECURE TX / TX state machine, and a
// corner seven-segment clock.

const STEP_HZ = 12500; // tuning step for up/down (12.5 kHz)

function regionFor(hz: number): { label: string; signal: number } {
  // Client-side approximation of the band profile for the signal bar (§3.2.2).
  if (hz < 10e6) return { label: "Low HF", signal: 0.2 };
  if (hz < 30e6) return { label: "High HF", signal: 0.45 };
  if (hz < 300e6) return { label: "VHF", signal: 0.8 };
  return { label: "UHF", signal: 0.95 };
}

function formatMHz(hz: number): string {
  return (hz / 1e6).toFixed(3);
}

export function Radio({
  socket,
  login,
  timezone,
  onOpenAar,
}: {
  socket: PivotSocket;
  login: LoginResponse;
  timezone: string;
  onOpenAar: () => void;
}) {
  const [freqHz, setFreqHz] = useState(login.frequency_hz);
  const [mode, setMode] = useState<RadioMode>(login.mode);
  const [phase, setPhase] = useState<TxPhase>("IDLE");
  const [entry, setEntry] = useState(formatMHz(login.frequency_hz));
  const mic = useRef(new MicCapture());
  const region = regionFor(freqHz);
  const transmitting = phase !== "IDLE";

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
      await mic.current.start(); // capture handed to WebRTC transport (scaffold)
    } catch {
      /* permission denied: control still proceeds; no audio reaches the net */
    }
    socket.pttStart(`${formatMHz(freqHz)} MHz`, mode);
  }, [socket, freqHz, mode, transmitting]);

  const endTx = useCallback(() => {
    playClick(700);
    mic.current.stop();
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
    const clamped = Math.max(1.6e6, Math.min(3e9, hz));
    setFreqHz(clamped);
    setEntry(formatMHz(clamped));
    socket.tune(`${formatMHz(clamped)} MHz`);
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
            <button className="btn btn--step" onClick={() => applyTune(freqHz - STEP_HZ)} disabled={transmitting}>
              ▼
            </button>
            <input
              className="input mono freq__entry"
              value={entry}
              disabled={transmitting}
              onChange={(e) => setEntry(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  const v = parseFloat(entry);
                  if (!isNaN(v)) applyTune(v * 1e6);
                }
              }}
            />
            <button className="btn btn--step" onClick={() => applyTune(freqHz + STEP_HZ)} disabled={transmitting}>
              ▲
            </button>
          </div>
        </div>

        <div className="radio__row">
          <button
            className={`toggle ${mode === "Cypher" ? "toggle--cypher" : "toggle--plain"}`}
            onClick={toggleMode}
            disabled={transmitting}
            title="Plain / Cypher (persists across retuning)"
          >
            {mode === "Cypher" ? "🔒 CYPHER" : "◌ PLAIN"}
          </button>

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

      <footer className="radio__footer">
        <button className="btn btn--ghost" onClick={onOpenAar}>
          After Action Review →
        </button>
      </footer>
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
