import { useEffect, useState } from "react";
import { api } from "../api";

// Login view (spec §3.2.1, §7.2.1). Trainees enter a callsign (no password);
// the instructor chooses "Log in as instructor" and enters the password.
const NAME_RE = /^[A-Za-z0-9 -]{1,32}$/;

export function Login({
  onTrainee,
  onInstructor,
}: {
  onTrainee: (name: string) => Promise<void> | void;
  onInstructor: (password: string) => Promise<void>;
}) {
  const [mode, setMode] = useState<"trainee" | "instructor">("trainee");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [online, setOnline] = useState<boolean | null>(null);
  const [micOk, setMicOk] = useState<boolean | null>(null);
  const [micInsecure, setMicInsecure] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.status().then(() => setOnline(true)).catch(() => setOnline(false));
  }, []);

  const valid = NAME_RE.test(name.trim());

  async function checkMic() {
    // The microphone API only exists in a *secure context* (https or
    // localhost) — over plain http:// at a LAN address, `navigator.mediaDevices`
    // is simply undefined and the browser never shows a permission prompt.
    // Detect that case up front so we can point at the real fix instead of
    // letting it fall into the same bucket as "permission denied".
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      setMicInsecure(true);
      setMicOk(false);
      return;
    }
    setMicInsecure(false);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((t) => t.stop());
      setMicOk(true);
    } catch {
      setMicOk(false);
    }
  }

  async function submitInstructor() {
    setError("");
    setBusy(true);
    try {
      await onInstructor(password);
    } catch {
      setError("Incorrect password.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <div className="card login__card">
        <h1 className="login__title">PIVOT</h1>
        <p className="login__subtitle">Procedural Interactive Voice Operations Trainer</p>

        {mode === "trainee" ? (
          <>
            <label className="field">
              <span>Name / Callsign</span>
              <input
                className="input mono"
                value={name}
                maxLength={32}
                placeholder="e.g. ALPHA-1"
                autoFocus
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && valid && onTrainee(name.trim())}
                aria-invalid={name.trim().length > 0 && !valid}
                aria-describedby="callsign-error"
              />
            </label>
            {name.trim().length > 0 && !valid && (
              <p id="callsign-error" className="login__hint">
                Only letters, numbers, spaces, and hyphens allowed.
              </p>
            )}
            <button
              className="btn btn--primary"
              disabled={!valid}
              onClick={() => onTrainee(name.trim())}
            >
              Join Net
            </button>
            <button className="btn btn--ghost login__switch" onClick={() => { setMode("instructor"); setError(""); }}>
              Log in as instructor →
            </button>
          </>
        ) : (
          <>
            <label className="field">
              <span>Instructor Password</span>
              <input
                className="input mono"
                type="password"
                value={password}
                autoFocus
                placeholder="default: instructor"
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && password && submitInstructor()}
              />
            </label>
            {error && <p className="login__hint">{error}</p>}
            <button className="btn btn--primary" disabled={busy || !password} onClick={submitInstructor}>
              {busy ? "Signing in…" : "Sign In"}
            </button>
            <button className="btn btn--ghost login__switch" onClick={() => { setMode("trainee"); setError(""); }}>
              ← Back to trainee login
            </button>
          </>
        )}

        <div className="login__status">
          <StatusDot ok={online} label={online == null ? "Connecting…" : online ? "Server online" : "Server unreachable"} />
          {mode === "trainee" && (
            <button className="btn btn--ghost" onClick={checkMic}>
              {micOk == null ? "Check microphone" : micOk ? "Microphone OK" : "Microphone blocked"}
            </button>
          )}
        </div>
        {mode === "trainee" && micOk === false && (
          <p className="login__hint">
            {micInsecure ? (
              <>
                This connection isn't secure, so the browser won't allow microphone
                access at all (no permission prompt will appear). If the address
                bar shows <code>http://</code>, ask the instructor for the{" "}
                <code>https://</code> address instead — the browser will warn that
                it isn't verified (PIVOT uses a private, self-signed certificate);
                choose <strong>Advanced → Proceed</strong> once, then reload this page.
              </>
            ) : (
              "Allow microphone access in your browser to transmit."
            )}
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
