import { useEffect, useRef, useState } from "react";
import { api, getToken, setToken } from "./api";
import type { LoginResponse } from "./types";
import { PivotSocket } from "./ws";
import { Login } from "./views/Login";
import { Radio } from "./views/Radio";
import { InstructorConsole } from "./views/InstructorConsole";

type View = "login" | "trainee" | "instructor";

// Browser-stored trainee id so the same terminal keeps its identity (and its
// server-side persistent crypto mode) across reloads (spec §3.2.1, §3.4.4).
function traineeId(): string {
  let id = localStorage.getItem("pivot_trainee_id");
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem("pivot_trainee_id", id);
  }
  return id;
}

// Trainee callsign persistence: a refresh (or a server restart) must not drop an
// operator out of a running scenario. We remember the callsign and "touch" it
// while the radio is open; an idle window longer than this expires it so a stale
// callsign from a previous day doesn't silently rejoin.
const TRAINEE_NAME_KEY = "pivot_trainee_name";
const TRAINEE_SEEN_KEY = "pivot_trainee_seen";
const TRAINEE_TTL_MS = 60 * 60 * 1000; // 1 hour of inactivity
// Slide the instructor token and trainee freshness well inside their TTLs.
const REFRESH_INTERVAL_MS = 15 * 60 * 1000;
const TOUCH_INTERVAL_MS = 5 * 60 * 1000;

function saveTrainee(name: string) {
  localStorage.setItem(TRAINEE_NAME_KEY, name);
  localStorage.setItem(TRAINEE_SEEN_KEY, String(Date.now()));
}
function touchTrainee() {
  if (localStorage.getItem(TRAINEE_NAME_KEY)) {
    localStorage.setItem(TRAINEE_SEEN_KEY, String(Date.now()));
  }
}
function loadTrainee(): string | null {
  const name = localStorage.getItem(TRAINEE_NAME_KEY);
  const seen = Number(localStorage.getItem(TRAINEE_SEEN_KEY) || 0);
  if (!name || Date.now() - seen > TRAINEE_TTL_MS) return null;
  return name;
}

export default function App() {
  const [view, setView] = useState<View>("login");
  const [login, setLogin] = useState<LoginResponse | null>(null);
  const [timezone, setTimezone] = useState("UTC");
  const [mustChange, setMustChange] = useState(false);
  // Restore prior login before deciding what to show, so a refresh doesn't flash
  // the login screen on the way back into a session.
  const [restoring, setRestoring] = useState(true);
  const socketRef = useRef<PivotSocket | null>(null);

  useEffect(() => {
    api.status().then((s) => setTimezone(s.display_timezone)).catch(() => {});
  }, []);

  async function joinTrainee(name: string) {
    const id = traineeId();
    const resp = await api.loginTrainee(name, id);
    setLogin(resp);
    const socket = new PivotSocket({ name, trainee_id: id });
    socket.on("timezone_update", (p) => setTimezone(p.timezone));
    socket.connect();
    socketRef.current = socket;
    saveTrainee(name);
    setView("trainee");
  }

  async function loginInstructor(password: string) {
    const resp = await api.loginInstructor(password); // throws on bad password
    setMustChange(!!resp.must_change_password);
    setView("instructor");
  }

  function logoutInstructor() {
    api.logout();
    setView("login");
  }

  // On first load, restore a prior session so a refresh or a server restart
  // doesn't log anyone out. An instructor token (in localStorage) is re-validated
  // by refreshing it; a remembered trainee callsign rejoins automatically.
  useEffect(() => {
    let cancelled = false;
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
          await joinTrainee(name);
          return;
        }
      } catch {
        setToken(null); // stale/expired instructor token — fall back to login
      } finally {
        if (!cancelled) setRestoring(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Slide the instructor token while the console is open so a long scenario never
  // expires mid-exercise (and a refresh/restart always finds a fresh token).
  useEffect(() => {
    if (view !== "instructor") return;
    const id = window.setInterval(() => {
      api.refreshToken().catch(() => {});
    }, REFRESH_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [view]);

  // Keep the remembered trainee callsign fresh while the radio is open.
  useEffect(() => {
    if (view !== "trainee") return;
    touchTrainee();
    const id = window.setInterval(touchTrainee, TOUCH_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [view]);

  useEffect(() => () => socketRef.current?.disconnect(), []);

  if (restoring) return null;

  if (view === "trainee" && login) {
    return <Radio socket={socketRef.current!} login={login} timezone={timezone} />;
  }
  if (view === "instructor") {
    return (
      <InstructorConsole
        timezone={timezone}
        mustChangePassword={mustChange}
        onTimezone={setTimezone}
        onLogout={logoutInstructor}
      />
    );
  }
  return <Login onTrainee={joinTrainee} onInstructor={loginInstructor} />;
}
