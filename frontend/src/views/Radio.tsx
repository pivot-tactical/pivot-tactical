import { useCallback, useEffect, useRef, useState } from "react";
import { RadioPanel, TraineeRadio } from "../components/RadioPanel";
import { SevenSegmentClock } from "../components/SevenSegmentClock";
import {
  AudioIO,
  parseTaggedAudio,
  pcmLevel,
  playClick,
  playSyncTone,
} from "../audio";
import { formatMHz } from "../freq";
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

  // PTT hotkey (§3.2.2): the numpad key with a radio's number keys that radio —
  // one key per radio, so any open net can be answered by hand without first
  // choosing which radio the keyboard is pointed at. Matched on e.code, which is
  // the physical key, so it works with Num Lock off.
  useEffect(() => {
    const numbered = (e: KeyboardEvent): TraineeRadio | undefined => {
      const m = e.code.match(/^Numpad([1-9])$/);
      return m ? radiosRef.current[parseInt(m[1], 10) - 1] : undefined;
    };
    const down = (e: KeyboardEvent) => {
      if (isTyping(e) || e.repeat) return;
      const r = numbered(e);
      if (r) {
        e.preventDefault();
        startTx(r);
      }
    };
    const up = (e: KeyboardEvent) => {
      if (isTyping(e)) return;
      const r = numbered(e);
      if (r) {
        e.preventDefault();
        endTx(r);
      }
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [startTx, endTx]);

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

function isTyping(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement;
  return el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA");
}
