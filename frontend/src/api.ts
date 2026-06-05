// REST client for the PIVOT API (spec §6.1).
import type { EventRow, LoginResponse, RadioState, SessionSummary } from "./types";

async function jsonFetch<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export interface ServerStatus {
  name: string;
  version: string;
  git_sha: string;
  session_active: boolean;
  terminals: number;
  display_timezone: string;
}

export const api = {
  status: () => jsonFetch<ServerStatus>("/api/status"),

  login: (name: string, traineeId?: string) =>
    jsonFetch<LoginResponse>("/api/login", {
      method: "POST",
      body: JSON.stringify({ name, trainee_id: traineeId ?? null }),
    }),

  tune: (radioId: string, frequency: string) =>
    jsonFetch<RadioState>("/api/radio/tune", {
      method: "POST",
      body: JSON.stringify({ radio_id: radioId, frequency }),
    }),

  setMode: (radioId: string, mode: string) =>
    jsonFetch<RadioState>("/api/radio/mode", {
      method: "POST",
      body: JSON.stringify({ radio_id: radioId, mode }),
    }),

  sessions: () => jsonFetch<SessionSummary[]>("/api/sessions"),

  events: (sessionId: string) =>
    jsonFetch<EventRow[]>(`/api/sessions/${sessionId}/events`),

  // Audio stream URL for an AAR event (clean/dirty, plain/cypher view) — §3.6.3.
  eventAudioUrl: (eventId: string, mode: "clean" | "dirty", view: "plain" | "cypher") =>
    `/api/events/${eventId}/audio?mode=${mode}&view=${view}`,

  exportUrl: (sessionId: string, fmt: "zip" | "text" | "csv") =>
    `/api/sessions/${sessionId}/export?fmt=${fmt}`,
};
