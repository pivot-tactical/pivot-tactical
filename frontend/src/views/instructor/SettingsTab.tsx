import React, { useEffect, useMemo, useState } from "react";
import { api } from "../../api";
import type { ReleaseInfo, UpdateStatus } from "../../api";
import { formatMHz } from "../../freq";
import { PivotSocket } from "../../ws";
import { getTimezoneOptions } from "./utils";

const fmtMHz = formatMHz;

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

export function RecordingsCard() {
  const [path, setPath] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api.recordingsLocation().then((r) => setPath(r.path)).catch(() => {});
  }, []);

  async function openFolder() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.openRecordingsFolder();
      setPath(r.path);
      setMsg(
        r.opened
          ? "Opened on the machine running PIVOT."
          : "Couldn’t open a file manager here — browse to the path below on the PIVOT server."
      );
    } catch {
      setMsg("Couldn’t open the folder. Use the path below.");
    } finally {
      setBusy(false);
    }
  }

  async function copyPath() {
    if (!path) return;
    try {
      await navigator.clipboard.writeText(path);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked (insecure origin) — the path is on screen to copy */
    }
  }

  return (
    <section className="card pad">
      <h3>Recordings</h3>
      <p className="muted" style={{ marginTop: 0 }}>
        Per-transmission WAVs, named by session and time so they’re easy to find
        in a file browser.
      </p>
      <div className="row gap" style={{ flexWrap: "wrap" }}>
        <button className="btn btn--primary" onClick={openFolder} disabled={busy}>
          {busy ? "Opening…" : "Open recordings folder"}
        </button>
        <button className="btn" onClick={copyPath} disabled={!path}>
          {copied ? "Copied ✓" : "Copy path"}
        </button>
      </div>
      {path && (
        <div className="muted mono mt" style={{ wordBreak: "break-all" }}>{path}</div>
      )}
      {msg && <p className="muted mt" style={{ fontSize: "0.85em" }}>{msg}</p>}
    </section>
  );
}

export function DownloadBar({ tag, progress }: {
  tag: string;
  progress?: { received: number; total: number | null } | null;
}) {
  const received = progress?.received ?? 0;
  const total = progress?.total ?? null;
  const pct = total && total > 0 ? Math.min(100, Math.round((received / total) * 100)) : null;
  return (
    <div className="mt">
      <p style={{ fontWeight: 600, margin: 0 }}>
        ⟳ Downloading {tag}…{" "}
        <span className="mono" style={{ fontWeight: 400 }}>
          {pct !== null
            ? `${pct}% (${fmtBytes(received)} / ${fmtBytes(total!)})`
            : received > 0 ? fmtBytes(received) : ""}
        </span>
      </p>
      <progress
        className="dl-progress mt"
        style={{ width: "100%" }}
        max={100}
        value={pct ?? undefined}
      />
    </div>
  );
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}

let _relFmt: Intl.DateTimeFormat | null = null;

export function relTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "just now";
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 45) return "just now";
  if (secs < 90) return "a minute ago";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hour${hrs > 1 ? "s" : ""} ago`;

  try {
    if (!_relFmt) {
      _relFmt = new Intl.DateTimeFormat([], {
        year: "numeric", month: "numeric", day: "numeric",
        hour: "numeric", minute: "numeric", second: "numeric"
      });
    }
    return _relFmt.format(new Date(iso));
  } catch {
    return new Date(iso).toLocaleString();
  }
}

export function SettingsTab({ mustChangePassword, onTimezone, socket, onRestart, sessionActive }: {
  mustChangePassword?: boolean;
  onTimezone: (tz: string) => void;
  socket: PivotSocket | null;
  onRestart?: () => void;
  sessionActive: boolean;
}) {
  const [cfg, setCfg] = useState<Record<string, any>>({});
  const [saved, setSaved] = useState(false);
  const [pw, setPw] = useState({ current: "", next: "" });
  const [pwMsg, setPwMsg] = useState("");
  const timezoneOptions = useMemo(getTimezoneOptions, []);

  const [upd, setUpd] = useState<UpdateStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [applying, setApplying] = useState<string | null>(null);
  const [staged, setStaged] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [dlProgress, setDlProgress] =
    useState<{ tag: string; received: number; total: number | null } | null>(null);
  const [applyErr, setApplyErr] = useState<string | null>(null);
  const [restartErr, setRestartErr] = useState<string | null>(null);
  const [showDowngrade, setShowDowngrade] = useState(false);
  const [showChoose, setShowChoose] = useState(false);

  const stagedTag = staged || upd?.staged_tag || upd?.auto_staged || null;
  const applyingTag = applying && !applying.includes(":") ? applying : null;

  const [retained, setRetained] = useState<{ tag: string; bytes: number }[] | null>(null);

  useEffect(() => {
    api.getConfig().then((c) => setCfg(c.config ?? {})).catch(() => {});
    api.bandProfile().then((b) => setCryptoOn(b.crypto_enabled)).catch(() => {});
  }, []);

  function absorb(result: UpdateStatus) {
    setUpd(result);
    if (result.staged_tag) setStaged(result.staged_tag);
    else if (result.auto_staged) setStaged(result.auto_staged);
    if (result.auto_update_error) setApplyErr(result.auto_update_error);
  }

  useEffect(() => {
    api.checkUpdates()
      .then((snap) => {
        absorb(snap);
        if (!snap.last_checked && !snap.checking) {
          setChecking(true);
          api.refreshUpdates().then(absorb).catch(() => {}).finally(() => setChecking(false));
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!showDowngrade) return;
    api.retainedVersions()
      .then((r) => setRetained(r.retained))
      .catch(() => setRetained([]));
  }, [showDowngrade]);

  useEffect(() => {
    if (!socket) return;
    const off = socket.on("update_status", (snap: UpdateStatus) => absorb(snap));
    const offDl = socket.on("update_progress", (p: { tag: string; received: number; total: number | null }) => {
      setDlProgress(p);
    });
    return () => { off?.(); offDl?.(); };
  }, [socket]);

  useEffect(() => {
    if (!upd || !upd.checking) return;
    const poll = window.setInterval(async () => {
      try {
        const s = await api.checkUpdates();
        absorb(s);
        if (!s.checking) clearInterval(poll);
      } catch {
        /* keep polling until done or navigating away */
      }
    }, 1000);
    return () => clearInterval(poll);
  }, [upd?.checking]);

  async function checkNow() {
    setChecking(true);
    setApplyErr(null);
    try {
      const s = await api.refreshUpdates();
      absorb(s);
    } catch {
      /* toast or inline warning if needed */
    } finally {
      setChecking(false);
    }
  }

  async function applyUpdate(release: ReleaseInfo) {
    setApplying(release.tag);
    setApplyErr(null);
    setDlProgress({ tag: release.tag, received: 0, total: null });
    try {
      const res = await api.applyUpdate(
        release.tag, release.asset_url, release.sha256_url,
        release.sig_url, release.asset_name,
      );
      if (res.staged) {
        setStaged(res.tag);
        setChosen(res.tag);
        setShowChoose(false);
      }
    } catch (e: any) {
      const msg = String(e?.message ?? "");
      if (msg.includes("401") || msg.includes("Unauthorized") || msg.includes("forbidden")) {
        setApplyErr("Session expired — please log back in to apply this update.");
      } else {
        setApplyErr(e?.message ?? "Update failed");
      }
    } finally {
      setApplying(null);
      setDlProgress(null);
    }
  }

  async function rollback(tag: string) {
    setApplying(`rollback:${tag}`);
    setApplyErr(null);
    try {
      const res = await api.rollbackUpdate(tag);
      if (res.staged) {
        setStaged(res.tag);
        setChosen(res.tag);
        setShowChoose(false);
      }
    } catch (e: any) {
      setApplyErr(e?.message ?? "Rollback failed");
    } finally {
      setApplying(null);
    }
  }

  async function deleteRetained(tag: string) {
    setApplying(`delete:${tag}`);
    try {
      const res = await api.deleteRetained(tag);
      setRetained(res.retained);
    } catch (e: any) {
      setApplyErr(e?.message ?? "Delete failed");
    } finally {
      setApplying(null);
    }
  }

  async function changePassword(e: React.FormEvent) {
    e.preventDefault();
    setPwMsg("");
    try {
      await api.changePassword(pw.current, pw.next);
      setPwMsg("Password updated.");
      setPw({ current: "", next: "" });
    } catch {
      setPwMsg("Password change failed.");
    }
  }

  const set = (k: string, v: any) => setCfg((c) => ({ ...c, [k]: v }));
  const [cryptoOn, setCryptoOn] = useState(true);

  async function saveSettings(e: React.FormEvent) {
    e.preventDefault();
    setSaved(false);
    const keys = ["whisper_model", "whisper_compute_type", "transcription_confidence_threshold",
                  "default_frequency_hz", "session_inactivity_timeout_min", "timezone", "auto_update"];
    const updates: Record<string, unknown> = {};
    for (const k of keys) {
      if (cfg[k] !== undefined) updates[k] = cfg[k];
    }
    try {
      const { applied } = await api.updateSettings(updates);
      setCfg((prev) => ({ ...prev, ...applied }));
      if (applied.timezone) onTimezone(applied.timezone as string);
      await api.updateBandProfile({ crypto_enabled: cryptoOn });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      /* inline warning if save fails */
    }
  }

  async function restart(force = false) {
    setRestartErr(null);
    try {
      await api.restartServer(force);
      onRestart?.();
    } catch (e: any) {
      const msg = String(e?.message ?? "");
      if (msg.startsWith("409")) {
        setRestartErr("A session is running. Use “Restart anyway” to apply now and disconnect trainees.");
      } else {
        setRestartErr(msg || "Restart failed.");
      }
    }
  }

  return (
    <div className="settings-layout">
      <div className="settings-layout__col">
      <section className="card pad">
        <h3>Instructor Password</h3>
        {mustChangePassword && (
          <p className="login__hint" style={{ marginBottom: "1rem" }}>
            You are using the default password. Please change it.
          </p>
        )}
        <form onSubmit={changePassword} className="settings-grid">
          <Field label="Current password">
            <input
              type="password"
              className="input"
              value={pw.current}
              onChange={(e) => setPw({ ...pw, current: e.target.value })}
            />
          </Field>
          <Field label="New password">
            <input
              type="password"
              className="input"
              value={pw.next}
              onChange={(e) => setPw({ ...pw, next: e.target.value })}
            />
          </Field>
          <button type="submit" className="btn btn--primary btn--tiny">Update Password</button>
          {pwMsg && <span className="muted">{pwMsg}</span>}
        </form>
      </section>

      <section className="card pad mt">
        <h3>System Settings</h3>
        <form onSubmit={saveSettings} className="settings-grid">
          <Field label="Display Timezone">
            <select
              className="input"
              value={cfg.timezone ?? "UTC"}
              onChange={(e) => set("timezone", e.target.value)}
            >
              {timezoneOptions.map((tz) => (
                <option key={tz} value={tz}>{tz}</option>
              ))}
            </select>
          </Field>

          <Field label="Default start frequency (MHz)">
            <input
              type="number"
              step="0.0001"
              min="0.1"
              max="1000"
              className="input mono"
              value={
                cfg.default_frequency_hz != null
                  ? (cfg.default_frequency_hz / 1e6).toFixed(4)
                  : "7.0000"
              }
              onChange={(e) => {
                const mhz = parseFloat(e.target.value);
                if (!isNaN(mhz)) set("default_frequency_hz", Math.round(mhz * 1e6));
              }}
            />
          </Field>

          <Field label="Session inactivity timeout (minutes)">
            <input
              type="number"
              min="1"
              max="1440"
              className="input mono"
              value={cfg.session_inactivity_timeout_min ?? 60}
              onChange={(e) => set("session_inactivity_timeout_min", parseInt(e.target.value, 10) || 60)}
            />
          </Field>

          <Field label="Enable Cypher mode">
            <input
              type="checkbox"
              checked={cryptoOn}
              onChange={(e) => setCryptoOn(e.target.checked)}
            />
          </Field>

          <Field label="Whisper Model">
            <select
              className="input"
              value={cfg.whisper_model ?? "tiny.en"}
              onChange={(e) => set("whisper_model", e.target.value)}
            >
              <option value="tiny.en">tiny.en (fastest, lowest RAM)</option>
              <option value="base.en">base.en (recommended)</option>
              <option value="small.en">small.en (higher accuracy)</option>
              <option value="medium.en">medium.en (high RAM usage)</option>
            </select>
          </Field>

          <Field label="Whisper Compute Type">
            <select
              className="input"
              value={cfg.whisper_compute_type ?? "int8"}
              onChange={(e) => set("whisper_compute_type", e.target.value)}
            >
              <option value="int8">int8 (fastest CPU inference)</option>
              <option value="float16">float16 (GPU recommended)</option>
              <option value="float32">float32 (full precision CPU)</option>
            </select>
          </Field>

          <Field label="Transcription Confidence Threshold">
            <input
              type="number"
              step="0.05"
              min="0"
              max="1"
              className="input mono"
              value={cfg.transcription_confidence_threshold ?? 0.4}
              onChange={(e) => set("transcription_confidence_threshold", parseFloat(e.target.value) || 0.4)}
            />
          </Field>

          <Field label="Automatic Update Checks & Staging">
            <input
              type="checkbox"
              checked={cfg.auto_update ?? true}
              onChange={(e) => set("auto_update", e.target.checked)}
            />
          </Field>

          <button type="submit" className="btn btn--primary btn--tiny">Save System Settings</button>
          {saved && <span className="muted">Settings saved ✓</span>}
        </form>
      </section>

      <div className="mt">
        <RecordingsCard />
      </div>
      </div>

      <div className="settings-layout__col">
      <section className="card pad">
        <h3>Software Updates</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          PIVOT checks for signed releases on GitHub. New releases are verified, staged,
          and applied on the next restart.
        </p>

        {/* Current status line */}
        <div className="row between" style={{ alignItems: "center" }}>
          <div>
            <div className="mono" style={{ fontSize: "1.1em", fontWeight: 600 }}>
              PIVOT v{upd?.current_version ?? "1.0.0"}
            </div>
            {upd?.last_checked && (
              <div className="muted" style={{ fontSize: "0.85em" }}>
                Last checked {relTime(upd.last_checked)}
              </div>
            )}
          </div>
          <button
            className="btn"
            onClick={checkNow}
            disabled={checking || upd?.checking || applying !== null}
          >
            {checking || upd?.checking ? "Checking…" : "Check for updates"}
          </button>
        </div>

        {/* Headline notice depending on state */}
        {(() => {
          if (dlProgress) return <DownloadBar tag={dlProgress.tag} progress={dlProgress} />;
          if (applyingTag)
            return (
              <div className="mt">
                <p style={{ fontWeight: 600, margin: 0 }}>
                  ⟳ Staging version {applyingTag}…
                </p>
                <progress className="dl-progress mt" style={{ width: "100%" }} />
              </div>
            );
          const hasStaged = !!stagedTag;
          if (hasStaged)
            return (
              <div className="upd-banner upd-banner--ready mt">
                <strong>
                  {chosen ? `Version ${chosen} selected and staged` : `${stagedTag} ready — restart PIVOT to apply`}
                </strong>
                <p className="muted" style={{ margin: "4px 0 0" }}>
                  The download is verified and staged on disk. Restart the server whenever
                  you're ready — trainees will reconnect automatically.
                </p>
              </div>
            );
          if (!upd) return null;
          if (!upd.reachable && !upd.checking)
            return <p className="login__hint mt">
              GitHub unreachable{upd.error ? <> — <code>{upd.error}</code></> : ""}.{" "}
              If your browser can reach the internet but this fails, the cause is
              usually a proxy, firewall or TLS-inspecting certificate that this
              server process doesn't see (browsers use the OS's settings; this
              check doesn't) — check the server's console/log for the same
              message, or use offline import.
            </p>;
          if (upd.reachable && upd.available.length === 0)
            return <p className="mt" style={{ fontWeight: 600 }}>You’re up to date.</p>;
          if (upd.reachable && upd.available.length > 0)
            return <p className="mt" style={{ fontWeight: 600 }}>
              {upd.available.length} newer release{upd.available.length > 1 ? "s" : ""} available
              {upd.auto_update ? " — will install automatically when no session is running." : ":"}
            </p>;
          return null;
        })()}

        {upd && stagedTag && (
          <button className="btn btn--ghost mt" onClick={() => setShowChoose((s) => !s)}>
            {showChoose ? "Keep the staged version" : "Choose a different version…"}
          </button>
        )}

        {upd && (!stagedTag || showChoose) && upd.reachable && upd.available.map((a) => (
          <div className="row between mt" key={a.tag}>
            <span className="mono">
              {a.tag}{a.prerelease ? " · prerelease" : ""}{a.tag === stagedTag ? " · staged" : ""}
            </span>
            {applying === a.tag ? (
              <span className="muted">Downloading…</span>
            ) : a.tag === stagedTag ? (
              <span className="muted">Staged — restart to apply</span>
            ) : a.has_asset ? (
              <button className="btn btn--primary" onClick={() => applyUpdate(a)}
                disabled={applying !== null}>
                Download &amp; install
              </button>
            ) : (
              <span className="muted">No build for this platform</span>
            )}
          </div>
        ))}
        {applyErr && <p className="login__hint mt">{applyErr}</p>}

        {upd && (!stagedTag || showChoose) && (
          <div className="mt">
            <button className="btn btn--ghost" onClick={() => setShowDowngrade((s) => !s)}>
              {showDowngrade ? "Hide downgrade options" : "Downgrade / recovery…"}
            </button>
            {showDowngrade && (
              <div className="mt">
                <p className="muted" style={{ fontSize: "0.85em" }}>
                  Stored on disk (instant rollback, no download):
                </p>
                {retained === null ? (
                  <p className="muted mt" style={{ fontSize: "0.85em" }}>Loading…</p>
                ) : retained.length === 0 ? (
                  <p className="muted mt" style={{ fontSize: "0.85em" }}>
                    No versions stored on disk yet. One is kept each time you update.
                  </p>
                ) : (
                  retained.map((v) => (
                    <div className="row between mt" key={v.tag} style={{ alignItems: "center" }}>
                      <span className="mono">{v.tag} · {fmtBytes(v.bytes)}</span>
                      <span className="row gap" style={{ alignItems: "center" }}>
                        {applying === `rollback:${v.tag}` ? (
                          <span className="muted">Staging…</span>
                        ) : (
                          <button className="btn btn--danger" onClick={() => rollback(v.tag)}
                            disabled={applying !== null}>
                            Roll back
                          </button>
                        )}
                        {applying === `delete:${v.tag}` ? (
                          <span className="muted">Deleting…</span>
                        ) : (
                          <button className="btn btn--ghost" onClick={() => deleteRetained(v.tag)}
                            disabled={applying !== null} title="Delete from disk to free space">
                            Delete
                          </button>
                        )}
                      </span>
                    </div>
                  ))
                )}
                <p className="muted mt" style={{ fontSize: "0.85em" }}>
                  Or install any earlier version (re-downloads &amp; verifies it):
                </p>
                {(upd.releases || []).filter((r) => r.standing === "older").map((a) => (
                  <div className="row between mt" key={a.tag}>
                    <span className="mono">
                      {a.tag}{a.prerelease ? " · prerelease" : ""}{a.tag === stagedTag ? " · staged" : ""}
                    </span>
                    {applying === a.tag ? (
                      <span className="muted">Downloading…</span>
                    ) : a.tag === stagedTag ? (
                      <span className="muted">Staged — restart to apply</span>
                    ) : a.has_asset ? (
                      <button className="btn" onClick={() => applyUpdate(a)} disabled={applying !== null}>
                        Install this version
                      </button>
                    ) : (
                      <span className="muted">No build for this platform</span>
                    )}
                  </div>
                ))}
                {(upd.releases || []).filter((r) => r.standing === "older").length === 0 && (
                  <p className="muted mt" style={{ fontSize: "0.85em" }}>
                    No earlier versions available to download.
                  </p>
                )}
                <p className="muted mt" style={{ fontSize: "0.8em" }}>
                  Tip: if a bad update won’t even start, run
                  <span className="mono"> PIVOT-Tactical --rollback </span>
                  from the install folder to recover.
                </p>
              </div>
            )}
          </div>
        )}

        {(() => {
          const hasStaged = !!stagedTag;
          return (
            <div className="row gap mt" style={{ alignItems: "center" }}>
              <button
                className={`btn ${hasStaged ? "btn--primary" : ""}`}
                onClick={() => restart(false)}
              >
                {hasStaged ? "Restart now to apply" : "Restart server"}
              </button>
              {restartErr && (
                <button className="btn btn--danger" onClick={() => restart(true)}>
                  Restart anyway
                </button>
              )}
            </div>
          );
        })()}
        {restartErr && <p className="login__hint mt">{restartErr}</p>}
        {sessionActive && !restartErr && (
          <p className="muted mt" style={{ fontSize: "0.85em" }}>
            A session is running — restarting will disconnect trainees, so it’s guarded.
          </p>
        )}

        <p className="muted mt" style={{ fontSize: "0.85em" }}>
          Updates are verified (checksum + signature), staged, and applied on the
          next restart — out-of-band, never mid-session. Air-gapped sites can use
          offline import.
        </p>
      </section>
      </div>
    </div>
  );
}
