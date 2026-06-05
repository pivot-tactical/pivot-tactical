import { useEffect, useState } from "react";
import { api } from "../api";

// Login view (spec §3.2.1, §7.2.1): single callsign field, no password.
// Includes the mic-permission check called out in the risk register (§10).
const NAME_RE = /^[A-Za-z0-9 -]{1,32}$/;

export function Login({ onJoin }: { onJoin: (name: string) => void }) {
  const [name, setName] = useState("");
  const [online, setOnline] = useState<boolean | null>(null);
  const [micOk, setMicOk] = useState<boolean | null>(null);

  useEffect(() => {
    api
      .status()
      .then(() => setOnline(true))
      .catch(() => setOnline(false));
  }, []);

  const valid = NAME_RE.test(name.trim());

  async function checkMic() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((t) => t.stop());
      setMicOk(true);
    } catch {
      setMicOk(false);
    }
  }

  return (
    <div className="login">
      <div className="card login__card">
        <h1 className="login__title">PIVOT</h1>
        <p className="login__subtitle">Procedural Interactive Voice Operations Trainer</p>

        <label className="field">
          <span>Name / Callsign</span>
          <input
            className="input mono"
            value={name}
            maxLength={32}
            placeholder="e.g. ALPHA-1"
            autoFocus
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && valid && onJoin(name.trim())}
          />
        </label>

        <button className="btn btn--primary" disabled={!valid} onClick={() => onJoin(name.trim())}>
          Join Net
        </button>

        <div className="login__status">
          <StatusDot ok={online} label={online == null ? "Connecting…" : online ? "Server online" : "Server unreachable"} />
          <button className="btn btn--ghost" onClick={checkMic}>
            {micOk == null ? "Check microphone" : micOk ? "Microphone OK" : "Microphone blocked"}
          </button>
        </div>
        {micOk === false && (
          <p className="login__hint">
            Allow microphone access in your browser to transmit. Use a Chromium-based browser for best results.
          </p>
        )}
      </div>
    </div>
  );
}

function StatusDot({ ok, label }: { ok: boolean | null; label: string }) {
  const cls = ok == null ? "dot--idle" : ok ? "dot--ok" : "dot--err";
  return (
    <span className="status-dot">
      <span className={`dot ${cls}`} /> {label}
    </span>
  );
}
