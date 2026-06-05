import { useEffect, useRef, useState } from "react";
import { api } from "./api";
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

export default function App() {
  const [view, setView] = useState<View>("login");
  const [login, setLogin] = useState<LoginResponse | null>(null);
  const [timezone, setTimezone] = useState("UTC");
  const [mustChange, setMustChange] = useState(false);
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

  useEffect(() => () => socketRef.current?.disconnect(), []);

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
