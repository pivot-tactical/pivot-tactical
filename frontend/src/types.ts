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
  timestamp_start: string;
  duration_ms: number;
  transcription: string | null;
  transcription_confidence: number | null;
  transcription_status: string;
}

// The trainee radio state machine shown over the PTT control (§3.2.2, §7.2.2).
export type TxPhase = "IDLE" | "CRYPTO_SYNC" | "SECURE_TX" | "TX";
