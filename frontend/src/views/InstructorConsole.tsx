import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { AudioIO, parseTaggedAudio, pcmLevel } from "../audio";
import { ConnectionBanner } from "../components/ConnectionBanner";
import type { ConnState } from "../components/ConnectionBanner";
import { SevenSegmentClock } from "../components/SevenSegmentClock";
import type { EventRow, LogEntry, NetScenario, RadioState, SessionLogMarker, Terminal } from "../types";
import { PivotSocket } from "../ws";
import { AarTab } from "./instructor/AarTab";
import { MonitorTab } from "./instructor/MonitorTab";
import { RadiosTab, updateRadio } from "./instructor/RadiosTab";
import { SettingsTab } from "./instructor/SettingsTab";
import { Tab, timestampOf } from "./instructor/utils";

export function InstructorConsole({
  timezone,
  mustChangePassword,
  onTimezone,
  onLogout,
}: {
  timezone: string;
  mustChangePassword?: boolean;
  onTimezone: (tz: string) => void;
  onLogout: () => void;
}) {
  const [tab, setTab] = useState<Tab>(mustChangePassword ? "settings" : "radios");
  const [radios, setRadios] = useState<RadioState[]>([]);
  const [terminals, setTerminals] = useState<Terminal[]>([]);
  const [netScenarios, setNetScenarios] = useState<NetScenario[]>([]);
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [sessionActive, setSessionActive] = useState(false);
  const [sessionName, setSessionName] = useState("");
  const [conn, setConn] = useState<ConnState>("online");

  const restartingRef = useRef(false);
  const restartPollRef = useRef<number | undefined>(undefined);
  const socketRef = useRef<PivotSocket | null>(null);

  const audio = useRef(new AudioIO());
  const rxLevels = useRef<Record<string, number>>({});

  function startRestartPoll() {
    if (restartPollRef.current) return;
    setConn("restarting");
    restartPollRef.current = window.setInterval(async () => {
      try {
        await api.checkUpdates();
        window.clearInterval(restartPollRef.current);
        restartPollRef.current = undefined;
        restartingRef.current = false;
        window.location.reload();
      } catch {
        /* keep polling until server responds */
      }
    }, 1500);
  }

  useEffect(() => {
    const sock = new PivotSocket(() => ({}));
    socketRef.current = sock;
    sock.connect();

    sock.onAudio((buf) => {
      const { radioId, pcm } = parseTaggedAudio(buf);
      rxLevels.current[radioId] = Math.max(rxLevels.current[radioId] ?? 0, pcmLevel(pcm));
      audio.current.play(pcm, radioId);
    });

    sock.on("conn_state", (st) => {
      if (st === "offline" && restartingRef.current) {
        startRestartPoll();
        return;
      }
      setConn(st as ConnState);
    });

    sock.on("instructor_radios", (list: RadioState[]) => setRadios(list));
    sock.on("radio_state", (r: RadioState) => setRadios((prev) => updateRadio(prev, r)));
    sock.on("terminals", (ts: Terminal[]) => setTerminals(ts));
    sock.on("net_scenarios", (scs: NetScenario[]) => setNetScenarios(scs));

    sock.on("recent_events", (history: EventRow[]) => {
      const historyEntries: LogEntry[] = history.map((event) => ({ kind: "event", event }));
      setEntries((prev) => {
        const markers: LogEntry[] = [];
        for (const e of prev) {
          if (e.kind === "session") markers.push(e);
        }
        const seenEvents = new Set(prev.filter((e) => e.kind === "event").map((e) => (e as { kind: "event"; event: EventRow }).event.event_id));
        const newHistory = historyEntries.filter((e) => !seenEvents.has((e.event as EventRow).event_id));
        const merged = [...newHistory, ...prev];
        merged.sort((a, b) => timestampOf(b).localeCompare(timestampOf(a)));
        return merged;
      });
    });

    sock.on("event_logged", (event: EventRow) => {
      setEntries((prev) => {
        const idx = prev.findIndex((e) => e.kind === "event" && e.event.event_id === event.event_id);
        if (idx !== -1) {
          const updated = [...prev];
          updated[idx] = { kind: "event", event };
          return updated;
        }
        return [{ kind: "event", event }, ...prev];
      });
    });

    sock.on("session_marker", (marker: SessionLogMarker) => {
      setEntries((prev) => {
        const exists = prev.some(
          (e) => e.kind === "session" && e.marker.session_id === marker.session_id && e.marker.type === marker.type
        );
        if (exists) return prev;
        const merged = [{ kind: "session", marker }, ...prev];
        merged.sort((a, b) => timestampOf(b).localeCompare(timestampOf(a)));
        return merged;
      });
      if (marker.type === "started") {
        setSessionActive(true);
        setSessionName(marker.session_name);
      } else if (marker.type === "stopped") {
        setSessionActive(false);
        setSessionName("");
      }
    });

    api.status()
      .then((s: any) => {
        if (s.session_active) {
          setSessionActive(true);
          setSessionName(s.session_name ?? "");
        }
      })
      .catch(() => {});

    api.instructorRadios()
      .then((list) => setRadios(list))
      .catch(() => {});

    api.recentEvents()
      .then((history) => {
        const historyEntries: LogEntry[] = history.map((event) => ({ kind: "event", event }));
        setEntries((prev) => {
          const seenEvents = new Set(
            prev.filter((e) => e.kind === "event").map((e) => (e as { kind: "event"; event: EventRow }).event.event_id)
          );
          const seenMarkers = new Set(
            prev.filter((e) => e.kind === "session").map((e) => {
              const m = (e as { kind: "session"; marker: SessionLogMarker }).marker;
              return `${m.session_id}:${m.type}`;
            })
          );
          const newHistory = historyEntries.filter((e) => !seenEvents.has((e.event as EventRow).event_id));
          const existingMarkers = prev.filter((e) => e.kind === "session" && seenMarkers.has(`${(e as { kind: "session"; marker: SessionLogMarker }).marker.session_id}:${(e as { kind: "session"; marker: SessionLogMarker }).marker.type}`));
          const merged = [...newHistory, ...existingMarkers];
          merged.sort((a, b) => timestampOf(b).localeCompare(timestampOf(a)));
          return merged;
        });
      })
      .catch(() => {});

    api.terminals()
      .then((data) => setTerminals(data.terminals ?? []))
      .catch(() => {});

    return () => {
      sock.disconnect();
      socketRef.current = null;
      if (restartPollRef.current) window.clearInterval(restartPollRef.current);
    };
  }, []);

  const updateEvent = useCallback((ev: Partial<EventRow> & { event_id: string }) => {
    setEntries((prev) =>
      prev.map((e) => {
        if (e.kind !== "event" || e.event.event_id !== ev.event_id) return e;
        return { kind: "event", event: { ...e.event, ...ev } };
      })
    );
  }, []);

  function enterRestarting() {
    restartingRef.current = true;
    startRestartPoll();
  }

  async function handleStartSession() {
    const name = sessionName.trim() || undefined;
    const res = await api.startSession(name);
    setSessionActive(true);
    setSessionName(res.name ?? name ?? "Session");
  }

  async function handleStopSession() {
    if (!window.confirm("Are you sure you want to stop the current session?")) return;
    await api.endSession();
    setSessionActive(false);
    setSessionName("");
  }

  return (
    <div className="app instr-app" onClick={() => audio.current.prewarm().catch(() => {})}>
      <ConnectionBanner state={conn} />
      <header className="instr-header">
        <div className="header__title">
          <span className="logo">PIVOT</span>
          <span className="subtitle">INSTRUCTOR CONSOLE</span>
        </div>

        <div className="instr-session flex items-center gap-2">
          {sessionActive ? (
            <>
              <span className="session-badge active mono">
                ● SESSION: {sessionName || "ACTIVE"}
              </span>
              <button className="btn btn--danger btn--tiny" onClick={handleStopSession}>
                Stop Session
              </button>
            </>
          ) : (
            <>
              <input
                type="text"
                placeholder="Session name"
                className="input input--tiny mono"
                value={sessionName}
                onChange={(e) => setSessionName(e.target.value)}
              />
              <button className="btn btn--primary btn--tiny" onClick={handleStartSession}>
                Start Session
              </button>
            </>
          )}
        </div>

        <nav className="header__nav" role="tablist">
          {(["radios", "monitor", "aar", "settings"] as const).map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              className={`nav__tab ${tab === t ? "nav__tab--active" : ""}`}
              onClick={() => setTab(t)}
            >
              {t === "aar" ? "AAR" : t.charAt(0).toUpperCase() + t.slice(1)}
            </button>
          ))}
        </nav>

        <div className="header__clock">
          <SevenSegmentClock timezone={timezone} />
        </div>

        <button className="btn btn--ghost btn--tiny" onClick={onLogout}>Logout</button>
      </header>

      <main className="instr-main">
        {tab === "radios" && (
          <RadiosTab
            radios={radios}
            socket={socketRef.current}
            audio={audio.current}
            onChange={setRadios}
            entries={entries}
            timezone={timezone}
            netScenarios={netScenarios}
            rxLevels={rxLevels}
            onEventUpdate={updateEvent}
          />
        )}
        {tab === "monitor" && <MonitorTab terminals={terminals} />}
        {tab === "aar" && <AarTab timezone={timezone} />}
        {tab === "settings" && (
          <SettingsTab
            mustChangePassword={mustChangePassword}
            onTimezone={onTimezone}
            socket={socketRef.current}
            onRestart={enterRestarting}
            sessionActive={sessionActive}
          />
        )}
      </main>
    </div>
  );
}
