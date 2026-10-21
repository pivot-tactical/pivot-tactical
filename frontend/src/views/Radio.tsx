import { useEffect, useRef, useState } from "react";
import { RadioPanel, TraineeRadio } from "../components/RadioPanel";
import { SevenSegmentClock } from "../components/SevenSegmentClock";
import {
  AudioIO,
  parseTaggedAudio,
  pcmLevel,
} from "../audio";
import { formatMHz } from "../freq";
import type { LoginResponse, RadioMode, RadioState } from "../types";
import { PivotSocket } from "../ws";
import { ConnectionBanner, type ConnState } from "../components/ConnectionBanner";
import { AudioControls, useMicState } from "../components/AudioControls";
import { usePtt, shortcutLabel } from "../usePtt";
import { PttShortcuts } from "../components/PttShortcuts";

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

const MAX_RADIOS = 9; // matches the server cap and the numpad PTT hotkeys

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
  const [focused, setFocused] = useState<string | null>(null);
  const audio = useRef(new AudioIO());
  const mic = useMicState(audio.current);
  const [conn, setConn] = useState<ConnState>(() => socket.connected ? "online" : "connecting");
  const [restoringRadios, setRestoringRadios] = useState<string[]>([]);
  const [controlError, setControlError] = useState("");
  const controlsReady = conn === "online" && restoringRadios.length === 0;
  const canTransmit = controlsReady && mic.status === "ready";
  const { phases, start, end, shortcuts, setShortcut, error: pttError } = usePtt({
    audio: audio.current,
    socket,
    radios: radios.map((r) => ({ id: r.radioId, frequency: `${formatMHz(r.freqHz)} MHz`, mode: r.mode })),
    enabled: canTransmit,
  });
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
    return () => {
      window.removeEventListener("pointerdown", enable);
      window.removeEventListener("keydown", enable);
      io.close();
    };
  }, [socket]);

  // --- WebSocket-driven state machine (§3.2.3) ---
  useEffect(() => {
    // Messages about the terminal's own radio may omit the id.
    const at = (p: any): string => p?.radio_id ?? primaryId;
    const patch = (id: string, fields: Partial<TraineeRadio>) =>
      setRadios((prev) => prev.map((r) => (r.radioId === id ? { ...r, ...fields } : r)));

    const restore = (p: Record<string, any>) => {
      if (p.role !== "trainee") {
        setControlError("This connection belongs to an instructor. Reload to sign in again.");
        setConn("offline");
        return;
      }
      patch(primaryId, { freqHz: p.frequency_hz, mode: p.mode });
      const extra = radiosRef.current.filter((r) => r.slot > 1);
      setRestoringRadios(extra.map((r) => r.radioId));
      setControlError("");
      setConn("online");
      for (const r of extra) socket.addRadio(r.slot, `${formatMHz(r.freqHz)} MHz`, r.mode);
    };
    const offs = [
      socket.on("open", () => setConn("connecting")),
      socket.on("close", () => { setConn("offline"); setRestoringRadios([]); }),
      socket.on("welcome", restore),
      socket.on("error", (p) => setControlError(p?.detail || "The radio command failed. Please try again.")),
      socket.on("tuned", (p) => patch(at(p), { freqHz: p.frequency_hz })),
      socket.on("mode_changed", (p) => patch(at(p), { mode: p.mode })),
      socket.on("radio_added", (p: RadioState) => {
        setRestoringRadios((prev) => prev.filter((id) => id !== p.radio_id));
        setRadios((prev) => {
          const added = fromRadioState(p);
          const merged = prev.some((r) => r.radioId === added.radioId)
            ? prev.map((r) => (r.radioId === added.radioId ? added : r))
            : [...prev, added];
          return merged.sort((a, b) => a.slot - b.slot);
        });
      }),
      socket.on("radio_removed", (p) =>
        setRadios((prev) => prev.filter((r) => r.radioId !== p.radio_id))
      ),
    ];
    if (socket.connected && socket.welcome) restore(socket.welcome);
    return () => offs.forEach((off) => off && off());
  }, [socket, primaryId]);

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
    if (!window.confirm(`Are you sure you want to remove ${r.name}?`)) return;
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
      <ConnectionBanner state={conn} />
      <header className="radio__top">
        <div className="radio__call mono">{login.name} · {controlsReady ? "ON NET" : conn === "online" ? "RESTORING RADIOS…" : conn === "connecting" ? "CONNECTING…" : "OFFLINE"}</div>
        <SevenSegmentClock timezone={timezone} />
      </header>

      <AudioControls audio={audio.current} disabled={Object.values(phases).some((p) => p !== "IDLE")} />
      {(controlError || pttError) && <p role="alert" className="login__hint">{controlError || pttError}</p>}
      {restoringRadios.length > 0 && <p role="status">Restoring additional radios…</p>}
      {(conn === "offline" || (restoringRadios.length > 0 && controlError)) && (
        <button className="btn" onClick={() => { socket.disconnect(); socket.connect(); }}>Reconnect</button>
      )}
      <PttShortcuts radios={radios.map((r) => ({ id: r.radioId, name: r.name }))} shortcuts={shortcuts} onChange={setShortcut} />
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
            socket={socket}
            audio={audio.current}
            rxLevels={rxLevels}
            onTune={retune}
            onMode={changeMode}
            onStart={(r) => start(r.radioId)}
            onEnd={(r) => end(r.radioId)}
            controlsDisabled={!controlsReady}
            pttDisabled={!canTransmit}
            shortcut={shortcutLabel(shortcuts[i])}
            onFocus={() => setFocused((f) => (f === r.radioId ? null : r.radioId))}
            onRemove={removeRadio}
          />
        ))}
      </div>

      {radios.length < MAX_RADIOS && (
        <button className="radio__add" onClick={addRadio} disabled={!controlsReady}>
          + Add Radio
        </button>
      )}
    </div>
  );
}
