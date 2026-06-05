import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { LoginResponse } from "./types";
import { PivotSocket } from "./ws";
import { Login } from "./views/Login";
import { Radio } from "./views/Radio";
import { AAR } from "./views/AAR";

type View = "login" | "radio" | "aar";

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
  const socketRef = useRef<PivotSocket | null>(null);

  useEffect(() => {
    api.status().then((s) => setTimezone(s.display_timezone)).catch(() => {});
  }, []);

  async function join(name: string) {
    const id = traineeId();
    const resp = await api.login(name, id);
    setLogin(resp);
    const socket = new PivotSocket(name, id);
    socket.on("welcome", () => {});
    socket.on("timezone_update", (p) => setTimezone(p.timezone));
    socket.connect();
    socketRef.current = socket;
    setView("radio");
  }

  useEffect(() => () => socketRef.current?.disconnect(), []);

  if (view === "login" || !login) return <Login onJoin={join} />;
  if (view === "aar") return <AAR onBack={() => setView("radio")} />;
  return (
    <Radio
      socket={socketRef.current!}
      login={login}
      timezone={timezone}
      onOpenAar={() => setView("aar")}
    />
  );
}
