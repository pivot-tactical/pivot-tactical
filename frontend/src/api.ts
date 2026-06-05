// REST client for the PIVOT API (spec §6.1).
// Instructor endpoints require a bearer token obtained from instructor login.
import type {
  EventRow,
  LoginResponse,
  RadioState,
  SessionSummary,
  Terminal,
} from "./types";

let instructorToken: string | null = sessionStorage.getItem("pivot_token");

export function setToken(t: string | null) {
  instructorToken = t;
  if (t) sessionStorage.setItem("pivot_token", t);
  else sessionStorage.removeItem("pivot_token");
}
export function getToken() {
  return instructorToken;
}

function headers(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (instructorToken) h["Authorization"] = `Bearer ${instructorToken}`;
  return h;
}

async function jsonFetch<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, { headers: headers(), ...options });
  if (!res.ok) {
    throw new Error(`${res.status}: ${await res.text()}`);
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

function tokenQuery(extra = ""): string {
  const t = instructorToken ? `token=${encodeURIComponent(instructorToken)}` : "";
  return [extra, t].filter(Boolean).join("&");
}

export const api = {
  status: () => jsonFetch<ServerStatus>("/api/status"),

  // --- login ---
  loginTrainee: (name: string, traineeId?: string) =>
    jsonFetch<LoginResponse>("/api/login", {
      method: "POST",
      body: JSON.stringify({ role: "trainee", name, trainee_id: traineeId ?? null }),
    }),

  async loginInstructor(password: string): Promise<LoginResponse> {
    const resp = await jsonFetch<LoginResponse>("/api/login", {
      method: "POST",
      body: JSON.stringify({ role: "instructor", password }),
    });
    if (resp.token) setToken(resp.token);
    return resp;
  },

  async logout() {
    try {
      await jsonFetch("/api/logout", { method: "POST" });
    } finally {
      setToken(null);
    }
  },

  changePassword: (current_password: string, new_password: string) =>
    jsonFetch("/api/admin/password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    }),

  // --- trainee radio ---
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

  // --- instructor: radios ---
  instructorRadios: () =>
    jsonFetch<RadioState[]>("/api/admin/instructor-radios"),
  addInstructorRadio: (frequency: string, label?: string) =>
    jsonFetch<RadioState>("/api/admin/instructor-radios", {
      method: "POST",
      body: JSON.stringify({ frequency, label: label ?? null }),
    }),
  removeInstructorRadio: (radioId: string) =>
    jsonFetch(`/api/admin/instructor-radios/${radioId}`, { method: "DELETE" }),
  tuneInstructorRadio: (radioId: string, frequency: string) =>
    jsonFetch<RadioState>(`/api/admin/instructor-radios/${radioId}/tune`, {
      method: "POST",
      body: JSON.stringify({ frequency }),
    }),
  modeInstructorRadio: (radioId: string, mode: string) =>
    jsonFetch<RadioState>(`/api/admin/instructor-radios/${radioId}/mode`, {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),

  // --- instructor: session / monitor / scenario / settings ---
  startSession: (name: string) =>
    jsonFetch<{ id: string }>("/api/admin/session/start", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  endSession: () => jsonFetch("/api/admin/session/end", { method: "POST" }),
  terminals: () =>
    jsonFetch<{ session_active: boolean; session_id: string | null; terminals: Terminal[] }>(
      "/api/admin/terminals"
    ),
  scenario: (payload: Record<string, unknown>) =>
    jsonFetch("/api/admin/scenario", { method: "POST", body: JSON.stringify(payload) }),
  getConfig: () => jsonFetch<Record<string, unknown>>("/api/admin/config"),
  updateSettings: (updates: Record<string, unknown>) =>
    jsonFetch<{ applied: Record<string, unknown> }>("/api/admin/settings", {
      method: "POST",
      body: JSON.stringify(updates),
    }),

  // --- instructor: AAR / history ---
  sessions: () => jsonFetch<SessionSummary[]>("/api/sessions"),
  events: (sessionId: string) => jsonFetch<EventRow[]>(`/api/sessions/${sessionId}/events`),
  eventAudioUrl: (eventId: string, mode: "clean" | "dirty", view: "plain" | "cypher") =>
    `/api/events/${eventId}/audio?${tokenQuery(`mode=${mode}&view=${view}`)}`,
  exportUrl: (sessionId: string, fmt: "zip" | "text" | "csv") =>
    `/api/sessions/${sessionId}/export?${tokenQuery(`fmt=${fmt}`)}`,
};
