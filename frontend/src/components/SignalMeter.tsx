import { useEffect, useRef } from "react";

// A live receive-strength meter. Unlike a static propagation estimate, it is
// driven by the actual audio frames arriving from the server — the same signal
// the operator hears — so it flickers with static crashes, breathes with
// interference swells, sits near the bottom on a clean quiet channel, and
// jumps up and modulates with the voice when a station keys the net.
//
// `read` is polled on every animation frame and should return the current
// level (0–1), applying its own decay; the bar is written straight to the DOM
// so the ~60 Hz meter never re-renders React.
export function SignalMeter({ label, read }: { label: string; read: () => number }) {
  const fillRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let raf: number;
    const tick = () => {
      const level = Math.max(0, Math.min(1, read()));
      if (fillRef.current) fillRef.current.style.width = `${Math.round(level * 100)}%`;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [read]);

  return (
    <div className="signal">
      <span className="signal__label">{label}</span>
      <div className="signal__bar">
        <div ref={fillRef} className="signal__fill" style={{ width: "0%" }} />
      </div>
    </div>
  );
}

// Per-frame decay applied between audio frames (polled at ~60 Hz): fast enough
// that the bar falls back within ~150 ms of the channel going quiet, slow
// enough that 50 Hz PCM frames hold a steady floor without strobing.
export const METER_DECAY = 0.93;
