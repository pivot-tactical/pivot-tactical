import { useEffect, useRef } from "react";
import type { TxPhase } from "../types";

interface Props {
  phase: TxPhase;
  disabled?: boolean;
  label: string;
  hint: string;
  onStart: () => void;
  onEnd: () => void;
}

export function PttButton({ phase, disabled, label, hint, onStart, onEnd }: Props) {
  const active = useRef(false);
  const pointer = useRef<number | null>(null);
  const endRef = useRef(onEnd);
  endRef.current = onEnd;
  const release = () => {
    if (!active.current) return;
    active.current = false;
    pointer.current = null;
    endRef.current();
  };
  useEffect(() => { if (disabled) release(); }, [disabled]);
  useEffect(() => {
    const cancel = () => {
      if (!active.current) return;
      active.current = false;
      pointer.current = null;
      endRef.current();
    };
    const hidden = () => { if (document.visibilityState === "hidden") cancel(); };
    window.addEventListener("blur", cancel);
    document.addEventListener("visibilitychange", hidden);
    return () => {
      window.removeEventListener("blur", cancel);
      document.removeEventListener("visibilitychange", hidden);
    };
  }, []);
  useEffect(() => () => { if (active.current) endRef.current(); }, []);
  return (
    <button
      type="button"
      className={`ptt ptt--${phase.toLowerCase()}`}
      style={{ touchAction: "none", opacity: disabled ? 0.5 : undefined }}
      disabled={disabled}
      aria-label={label}
      aria-pressed={phase !== "IDLE"}
      onPointerDown={(e) => {
        if (disabled || active.current || e.button !== 0) return;
        e.preventDefault();
        pointer.current = e.pointerId;
        active.current = true;
        e.currentTarget.setPointerCapture?.(e.pointerId);
        onStart();
      }}
      onPointerUp={(e) => { if (e.pointerId === pointer.current) release(); }}
      onPointerCancel={(e) => { if (e.pointerId === pointer.current) release(); }}
      onLostPointerCapture={(e) => { if (e.pointerId === pointer.current) release(); }}
      onBlur={release}
      onKeyDown={(e) => {
        if (e.code !== "Space" && e.code !== "Enter") return;
        e.preventDefault();
        if (disabled || active.current || e.repeat) return;
        active.current = true;
        onStart();
      }}
      onKeyUp={(e) => {
        if (e.code !== "Space" && e.code !== "Enter") return;
        e.preventDefault();
        release();
      }}
    >
      <span className="ptt__state">{phase === "CRYPTO_SYNC" ? "CRYPTO SYNC…" : phase === "SECURE_TX" ? "SECURE TX" : phase === "TX" ? "TX" : "PUSH TO TALK"}</span>
      <span className="ptt__hint">{hint}</span>
    </button>
  );
}
