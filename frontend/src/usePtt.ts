import { useCallback, useEffect, useRef, useState } from "react";
import { AudioIO, playClick, playSyncTone } from "./audio";
import type { RadioMode, TxPhase } from "./types";
import type { PivotSocket } from "./ws";

export interface PttRadio {
  id: string;
  frequency: string;
  mode: RadioMode;
}

interface Options {
  audio: AudioIO;
  socket: PivotSocket | null;
  radios: PttRadio[];
  enabled: boolean;
  instructor?: boolean;
}

interface Hold {
  socket: PivotSocket;
  audio: AudioIO;
  instructor: boolean;
  started: boolean;
}

const defaults = Array.from({ length: 9 }, (_, i) => `Numpad${i + 1}`);
export const PTT_CODES = [
  ...defaults,
  ...Array.from({ length: 9 }, (_, i) => `Digit${i + 1}`),
  ..."ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("").map((letter) => `Key${letter}`),
];

export function shortcutLabel(code: string): string {
  return code.replace("Numpad", "NUMPAD ").replace("Digit", "").replace("Key", "");
}

function loadShortcuts(key: string): string[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(key) || "null");
    if (Array.isArray(saved) && saved.length === 9 &&
        saved.every((code) => typeof code === "string" && PTT_CODES.includes(code)) &&
        new Set(saved).size === saved.length) return saved;
  } catch { /* Unavailable storage must not prevent operating the radio. */ }
  return [...defaults];
}

function editing(target: EventTarget | null): boolean {
  return target instanceof HTMLElement &&
    (!!target.closest("input, textarea, select, [contenteditable=true], [role=textbox]") || target.isContentEditable);
}

function connected(socket: PivotSocket): boolean {
  return socket.connected !== false;
}

// The hold object, rather than the radio id alone, identifies an asynchronous
// key-up attempt. A released attempt can never key a later hold on the same radio.
export function usePtt(options: Options) {
  const latest = useRef(options);
  latest.current = options;
  const holds = useRef(new Map<string, Hold>());
  const awaitingStarts = useRef(new Map<string, Hold[]>());
  const awaitingEnds = useRef(new Map<string, number>());
  const phasesRef = useRef<Record<string, TxPhase>>({});
  const [phases, setPhases] = useState<Record<string, TxPhase>>({});
  const [error, setError] = useState<string | null>(null);
  const storageKey = `pivot.ptt.shortcuts.${options.instructor ? "instructor" : "trainee"}`;
  const [shortcuts, setShortcuts] = useState(() => loadShortcuts(storageKey));
  const shortcutsRef = useRef(shortcuts);
  shortcutsRef.current = shortcuts;
  const pressedKeys = useRef(new Map<string, string>());

  const patchPhase = useCallback((id: string, phase?: TxPhase) => {
    const next = { ...phasesRef.current };
    if (phase) next[id] = phase;
    else delete next[id];
    phasesRef.current = next;
    setPhases(next);
  }, []);

  const end = useCallback((id: string) => {
    const hold = holds.current.get(id);
    if (!hold) return;
    holds.current.delete(id);
    if (hold.started) {
      awaitingEnds.current.set(id, (awaitingEnds.current.get(id) ?? 0) + 1);
      if (phasesRef.current[id] === "CRYPTO_SYNC") {
        if (hold.instructor) hold.socket.instrPttAbort(id);
        else hold.socket.pttAbort(id);
      } else if (hold.instructor) hold.socket.instrPttEnd(id);
      else hold.socket.pttEnd(id);
      playClick(700);
    }
    patchPhase(id);
    if (holds.current.size === 0) hold.audio.stopCapture();
  }, [patchPhase]);

  const releaseAll = useCallback(() => {
    pressedKeys.current.clear();
    for (const id of [...holds.current.keys()]) end(id);
  }, [end]);

  const start = useCallback(async (id: string) => {
    const { socket, audio, radios, enabled, instructor } = latest.current;
    const radio = radios.find((r) => r.id === id);
    if (!socket || !enabled || !connected(socket) || !radio || holds.current.has(id)) return;
    const hold: Hold = { socket, audio, instructor: !!instructor, started: false };
    holds.current.set(id, hold);
    setError(null);
    try {
      await audio.startCapture((pcm) => {
        // Capture may start before the control handshake. Only forward when a
        // radio is actually keyed; a cancelled permission prompt sends no voice.
        if ([...holds.current.values()].some((h) => h.started && h.socket === socket)) socket.sendAudio(pcm);
      });
      if (holds.current.get(id) !== hold) {
        if (holds.current.size === 0) audio.stopCapture();
        return;
      }
      if (!latest.current.enabled || !connected(socket) ||
          !latest.current.radios.some((r) => r.id === id)) {
        end(id);
        return;
      }
      hold.started = true;
      awaitingStarts.current.set(id, [...(awaitingStarts.current.get(id) ?? []), hold]);
      playClick();
      if (instructor) socket.instrPttStart(id, radio.frequency, radio.mode);
      else socket.pttStart(radio.frequency, radio.mode, id);
    } catch (cause) {
      if (holds.current.get(id) !== hold) return;
      // One microphone feeds every radio. A capture failure invalidates the
      // shared input, so stop any other net that was already keyed as well.
      releaseAll();
      setError(cause instanceof Error ? cause.message : "Microphone unavailable. Check audio settings and try again.");
    }
  }, [end, releaseAll]);

  const setShortcut = useCallback((index: number, code: string) => {
    if (index < 0 || index >= defaults.length || !PTT_CODES.includes(code)) return;
    const duplicate = shortcutsRef.current.indexOf(code);
    if (duplicate !== -1 && duplicate !== index) return;
    releaseAll();
    const next = [...shortcutsRef.current];
    next[index] = code;
    shortcutsRef.current = next;
    setShortcuts(next);
    try { localStorage.setItem(storageKey, JSON.stringify(next)); } catch { /* memory fallback */ }
  }, [releaseAll, storageKey]);

  useEffect(() => {
    if (!options.enabled) releaseAll();
    const ids = new Set(options.radios.map((r) => r.id));
    for (const id of [...holds.current.keys()]) if (!ids.has(id)) end(id);
  }, [options.enabled, options.radios, releaseAll, end]);

  useEffect(() => {
    const socket = options.socket;
    if (!socket) return;
    const idOf = (p: { radio_id?: string }) => p.radio_id ?? latest.current.radios[0]?.id;
    const phase = (p: { radio_id?: string }, value: TxPhase) => {
      const id = idOf(p);
      if (id && holds.current.get(id)?.started) patchPhase(id, value);
    };
    const finished = (p: { radio_id?: string }) => {
      const id = idOf(p);
      if (!id) return;
      const expected = awaitingEnds.current.get(id) ?? 0;
      if (expected > 0) {
        awaitingEnds.current.set(id, expected - 1);
      }
      // End/abort messages acknowledge a release already applied locally.
      // They must never finish a subsequent hold on this same radio.
    };
    const offs = [
      socket.on("ptt_started", (p) => {
        const id = idOf(p);
        if (!id) return;
        const pending = awaitingStarts.current.get(id);
        const acknowledged = pending?.shift();
        if (!acknowledged || holds.current.get(id) !== acknowledged) return;
        phase(p, p.sync_applies ? "CRYPTO_SYNC" : "TX");
        if (p.sync_applies) playSyncTone();
      }),
      socket.on("secure_tx", (p) => {
        const id = idOf(p);
        if (id && phasesRef.current[id] === "CRYPTO_SYNC") phase(p, "SECURE_TX");
      }),
      socket.on("ptt_ended", finished),
      socket.on("ptt_aborted", finished),
      socket.on("close", () => {
        releaseAll();
        awaitingStarts.current.clear();
        awaitingEnds.current.clear();
      }),
      socket.on("error", (p) => {
        releaseAll();
        awaitingStarts.current.clear();
        awaitingEnds.current.clear();
        setError(p?.detail || "Transmission failed. Please try again.");
      }),
    ];
    return () => { offs.forEach((off) => off()); releaseAll(); };
  }, [options.socket, patchPhase, releaseAll]);

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.repeat || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || editing(e.target)) return;
      const index = shortcutsRef.current.indexOf(e.code);
      const radio = latest.current.radios[index];
      if (!radio || !latest.current.enabled) return;
      e.preventDefault();
      pressedKeys.current.set(e.code, radio.id);
      void start(radio.id);
    };
    const up = (e: KeyboardEvent) => {
      // Release the radio matched at key-down, even if focus or order changed.
      const id = pressedKeys.current.get(e.code);
      if (!id) return;
      pressedKeys.current.delete(e.code);
      e.preventDefault();
      end(id);
    };
    const hidden = () => { if (document.visibilityState === "hidden") releaseAll(); };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", releaseAll);
    window.addEventListener("pagehide", releaseAll);
    document.addEventListener("visibilitychange", hidden);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", releaseAll);
      window.removeEventListener("pagehide", releaseAll);
      document.removeEventListener("visibilitychange", hidden);
      releaseAll();
    };
  }, [start, end, releaseAll]);

  return { phases, start, end, releaseAll, shortcuts, setShortcut, error };
}
