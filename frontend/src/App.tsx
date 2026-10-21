import { useEffect, useRef, useState } from "react";
import { api, getToken, setToken } from "./api";
import { errorMessage, isAuthenticationError } from "./errors";
import type { LoginResponse } from "./types";
import { PivotSocket } from "./ws";
import { Login } from "./views/Login";
import { Radio } from "./views/Radio";
import { InstructorConsole } from "./views/InstructorConsole";
import {
  traineeId,
  saveTrainee,
  touchTrainee,
  loadTrainee,
} from "./traineeStorage";

type View = "login" | "trainee" | "instructor";

// Slide the instructor token and trainee freshness well inside their TTLs.
const REFRESH_INTERVAL_MS = 15 * 60 * 1000;
const TOUCH_INTERVAL_MS = 5 * 60 * 1000;

export default function App() {
  const [view, setView] = useState<View>("login");
  const [login, setLogin] = useState<LoginResponse | null>(null);
  const [timezone, setTimezone] = useState("UTC");
  const [mustChange, setMustChange] = useState(false);
  // Restore prior login before deciding what to show, so a refresh doesn't flash
  // the login screen on the way back into a session.
  const [restoring, setRestoring] = useState(true);
  const [restoreAttempt, setRestoreAttempt] = useState(0);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const logoutPending = useRef(false);
  const authRevision = useRef(0);
  const socketRef = useRef<PivotSocket | null>(null);

  useEffect(() => {
    api.status().then((s) => setTimezone(s.display_timezone)).catch(() => {});
  }, []);

  async function joinTrainee(name: string) {
    const id = traineeId();
    const resp = await api.loginTrainee(name, id);
    connectTrainee(resp, name, id);
  }

  function connectTrainee(resp: LoginResponse, name: string, id: string) {
    setLogin(resp);
    socketRef.current?.disconnect();
    const socket = new PivotSocket({ name, trainee_id: id });
    socket.on("timezone_update", (p) => setTimezone(p.timezone));
    socket.connect();
    socketRef.current = socket;
    saveTrainee(name);
    setView("trainee");
  }

  async function loginInstructor(password: string) {
    const resp = await api.loginInstructor(password); // throws on bad password
    authRevision.current++;
    setLogoutError(null);
    setMustChange(!!resp.must_change_password);
    setView("instructor");
  }

  async function logoutInstructor() {
    if (logoutPending.current) return;
    logoutPending.current = true;
    authRevision.current++;
    setLogoutError(null);
    try {
      await api.logout();
      setNotice("");
      setView("login");
    } catch (err) {
      setLogoutError(`Sign out failed. ${errorMessage(err, "The server could not confirm sign out.")} Use Log out to retry.`);
    } finally {
      logoutPending.current = false;
    }
  }

  // On first load, restore a prior session so a refresh or a server restart
  // doesn't log anyone out. An instructor token (in localStorage) is re-validated
  // by refreshing it; a remembered trainee callsign rejoins automatically.
  useEffect(() => {
    let cancelled = false;
    setRestoring(true);
    setRestoreError(null);
    (async () => {
      try {
        if (getToken()) {
          const resp = await api.refreshToken(); // throws (401) if no longer valid
          if (cancelled) return;
          setMustChange(!!resp.must_change_password);
          setView("instructor");
          return;
        }
        const name = loadTrainee();
        if (name) {
          const id = traineeId();
          const resp = await api.loginTrainee(name, id);
          if (cancelled) return;
          connectTrainee(resp, name, id);
          return;
        }
      } catch (err) {
        if (cancelled) return;
        if (isAuthenticationError(err)) {
          setToken(null);
          setNotice("Your instructor session has expired. Sign in again.");
        } else {
          setRestoreError(errorMessage(err, "Could not restore your session. Please try again."));
        }
      } finally {
        if (!cancelled) setRestoring(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [restoreAttempt]);

  // Slide the instructor token while the console is open so a long scenario never
  // expires mid-exercise (and a refresh/restart always finds a fresh token).
  useEffect(() => {
    if (view !== "instructor") return;
    let cancelled = false;
    const id = window.setInterval(() => {
      if (logoutPending.current) return;
      const revision = authRevision.current;
      api.refreshToken().catch((err) => {
        if (cancelled || revision !== authRevision.current || logoutPending.current) return;
        if (isAuthenticationError(err)) {
          setToken(null);
          setNotice("Your instructor session has expired. Sign in again.");
          setView("login");
        }
      });
    }, REFRESH_INTERVAL_MS);
    return () => { cancelled = true; window.clearInterval(id); };
  }, [view]);

  // Keep the remembered trainee callsign fresh while the radio is open.
  useEffect(() => {
    if (view !== "trainee") return;
    touchTrainee();
    const id = window.setInterval(touchTrainee, TOUCH_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [view]);

  useEffect(() => () => socketRef.current?.disconnect(), []);

  if (restoring || restoreError) {
    return (
      <div className="login">
        <div className="card login__card">
          <h1 className="login__title">PIVOT</h1>
          {restoring ? <p role="status">Restoring your session…</p> : <>
            <p role="alert" className="login__hint">{restoreError}</p>
            <button className="btn btn--primary" onClick={() => setRestoreAttempt((n) => n + 1)}>Retry session</button>
            <button className="btn btn--ghost login__switch" onClick={() => setRestoreError(null)}>Back to sign in</button>
          </>}
        </div>
      </div>
    );
  }

  if (view === "trainee" && login) {
    return <Radio socket={socketRef.current!} login={login} timezone={timezone} />;
  }
  if (view === "instructor") {
    return (
      <>
        {logoutError && <div role="alert" className="login__hint" style={{ padding: "10px 20px", margin: 0 }}>{logoutError}</div>}
        <InstructorConsole
          timezone={timezone}
          mustChangePassword={mustChange}
          onTimezone={setTimezone}
          onLogout={logoutInstructor}
        />
      </>
    );
  }
  return <Login onTrainee={joinTrainee} onInstructor={loginInstructor} notice={notice} />;
}
