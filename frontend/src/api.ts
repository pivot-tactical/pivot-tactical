// REST client for the PIVOT API (spec §6.1).
// Instructor endpoints require a bearer token obtained from instructor login.
import type {
  EventRow,
  LoginResponse,
  RadioState,
  SessionSummary,
  Terminal,
} from "./types";

// Persisted in localStorage (not sessionStorage) so the instructor stays logged
// in across a page refresh and a server restart. The token is short-lived and
// server-signed; the app refreshes it while the console is open (see App.tsx).
let instructorToken: string | null = localStorage.getItem("pivot_token");

export function setToken(t: string | null) {
  instructorToken = t;
  if (t) localStorage.setItem("pivot_token", t);
  else localStorage.removeItem("pivot_token");
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

export interface ReleaseInfo {
  tag: string;
  name?: string;
  prerelease: boolean;
  standing: string;
  published_at: string;
  has_asset: boolean;
  asset_url: string;
  sha256_url: string;
  sig_url: string;
  asset_name: string;
}

// Shape of the background update service's cached status (and the live
// `update_status` broadcast). `available` is the subset that is newer than the
// running build; `releases` is the full channel-filtered list.
export interface UpdateStatus {
  current_version: string;
  channel: string;
  auto_update: boolean;
  updater: "staged";
  reachable: boolean;
  error: string | null;
  last_checked?: string | null;
  checking?: boolean;
  auto_state?: "idle" | "deferred_session_active" | "downloading" | "applied" | "error";
  auto_message?: string;
  auto_staged?: string;
  auto_update_error?: string;
  releases?: ReleaseInfo[];
  available: ReleaseInfo[];
  retained?: string[];      // versions kept on disk for instant rollback
  previous?: string | null; // the most recent retained version
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

  // Slide the instructor session: swap a still-valid token for a fresh one.
  // Used on load (to confirm a stored token survived a refresh/restart and
  // restore the console) and on a timer while the console is open. Throws on
  // 401, which the caller treats as "logged out".
  async refreshToken(): Promise<{ token: string; must_change_password?: boolean }> {
    const resp = await jsonFetch<{ token: string; must_change_password?: boolean }>(
      "/api/auth/refresh",
      { method: "POST" }
    );
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
  checkUpdates: () => jsonFetch<UpdateStatus>("/api/admin/updates/check"),
  // Force an immediate, synchronous re-check (the "Check now" button). The
  // background service caches the result so subsequent checkUpdates() are cheap.
  refreshUpdates: () =>
    jsonFetch<UpdateStatus>("/api/admin/updates/refresh", { method: "POST" }),
  applyUpdate: (tag: string, assetUrl: string, sha256Url: string, sigUrl: string, assetName: string) =>
    jsonFetch<{
      staged?: boolean;
      already_staged?: boolean;
      tag: string;
      restart_required: boolean;
    }>("/api/admin/updates/apply", {
      method: "POST",
      body: JSON.stringify({
        tag, asset_url: assetUrl, sha256_url: sha256Url, sig_url: sigUrl, asset_name: assetName,
      }),
    }),
  // Roll back to a retained version (instant, offline downgrade). Omit `tag` to
  // roll back to the most recent retained version. Applied on the next restart.
  rollbackUpdate: (tag?: string) =>
    jsonFetch<{ staged: boolean; tag: string; rollback: boolean; restart_required: boolean }>(
      "/api/admin/updates/rollback",
      { method: "POST", body: JSON.stringify({ tag: tag ?? null }) }
    ),

  // Restart the server (applies a staged update on the way back up). `force`
  // overrides the guard that refuses to restart while a session is live.
  restartServer: (force = false) =>
    jsonFetch<{ restarting: boolean; mode: string; staged: string | null }>(
      "/api/admin/restart",
      { method: "POST", body: JSON.stringify({ force }) }
    ),

  // --- instructor: AAR / history ---
  sessions: () => jsonFetch<SessionSummary[]>("/api/sessions"),
  events: (sessionId: string) => jsonFetch<EventRow[]>(`/api/sessions/${sessionId}/events`),
  eventAudioUrl: (eventId: string, mode: "clean" | "dirty", view: "plain" | "cypher") =>
    `/api/events/${eventId}/audio?${tokenQuery(`mode=${mode}&view=${view}`)}`,
  exportUrl: (sessionId: string, fmt: "zip" | "text" | "csv") =>
    `/api/sessions/${sessionId}/export?${tokenQuery(`fmt=${fmt}`)}`,
};
