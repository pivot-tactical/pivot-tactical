// Shared types mirroring the PIVOT API (spec §6).

export type RadioMode = "Plain" | "Cypher";
export type Role = "trainee" | "instructor";

export interface LoginResponse {
  role: Role;
  token?: string | null;
  must_change_password?: boolean;
  trainee_id?: string;
  radio_id?: string;
  frequency?: string;
  frequency_hz?: number;
  mode?: RadioMode;
}

export interface RadioState {
  radio_id: string;
  name: string;
  is_instructor: boolean;
  frequency: string;
  frequency_hz: number;
  band_region: string;
  mode: RadioMode;
  status: string;
  // Receive-noise toggle (instructor radios, §3.1.5): false = this radio's
  // received audio is rendered without channel noise; the net itself — and
  // every other station on it — is unaffected. Absent on monitor snapshots.
  rx_noise?: boolean;
}

export interface Terminal extends RadioState {
  last_activity: string;
}

// Per-net instructor override (§3.1.5): interference level / jammer on one
// channel, controlled from the instructor radio panels.
export interface NetScenario {
  freq_hz: number;
  interference: number; // 0 (clean) .. 1 (severe)
  jammed: boolean;
}

export interface SessionSummary {
  id: string;
  name: string;
  started_at: string;
  ended_at: string | null;
  event_count: number | null;
}

export interface EventRow {
  event_id: string;
  trainee_name: string;
  frequency: string;
  band_region: string;
  tx_mode: RadioMode;
  audibility: string;
  sync_status: string;
  // Captured channel state (what "Play with noise" re-renders under): jammed
  // reflects whether jamming was active on this station's channel when it keyed.
  jammed: boolean;
  snr_db: number | null;
  timestamp_start: string;
  duration_ms: number;
  transcription: string | null;
  transcription_confidence: number | null;
  transcription_status: string;
}

// A divider row in the Running Event Log marking when a training session
// started or stopped (spans the full width, unlike an EventRow).
export interface SessionLogMarker {
  session_id: string;
  session_name: string;
  type: "started" | "ended";
  timestamp: string;
}

export type LogEntry =
  | { kind: "event"; event: EventRow }
  | { kind: "session"; marker: SessionLogMarker };


// The trainee radio state machine shown over the PTT control (§3.2.2, §7.2.2).
export type TxPhase = "IDLE" | "CRYPTO_SYNC" | "SECURE_TX" | "TX";
