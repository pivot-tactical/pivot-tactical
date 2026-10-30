import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { ApiError } from "../errors";
import type { EventRow, SessionSummary } from "../types";
import { AarReview } from "./AarReview";

vi.mock("../api", () => ({ api: {
  sessions: vi.fn(), events: vi.fn(), exportSession: vi.fn(), eventAudioUrl: vi.fn(),
} }));

const session: SessionSummary = {
  id: "exercise-1", name: "First exercise", started_at: "2026-10-20T12:00:00Z",
  ended_at: "2026-10-20T13:00:00Z", event_count: 2,
};
function event(overrides: Partial<EventRow> = {}): EventRow {
  return {
    event_id: "event-1", trainee_name: "ALPHA", frequency: "14.250 MHz", band_region: "HF",
    tx_mode: "Cypher", audibility: "Heard", sync_status: "Completed", jammed: false, snr_db: 12,
    timestamp_start: "2026-10-20T12:01:00Z", duration_ms: 2000,
    transcription: "First message", transcription_confidence: 0.4, transcription_status: "Done",
    transcription_original: null, transcription_edited: false, ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.sessions).mockResolvedValue([session]);
  vi.mocked(api.events).mockResolvedValue([
    event(), event({ event_id: "event-2", trainee_name: "BRAVO", frequency: "30.000 MHz", transcription: "Second message", transcription_confidence: 0.95 }),
  ]);
  vi.mocked(api.exportSession).mockResolvedValue(undefined);
  vi.mocked(api.eventAudioUrl).mockImplementation((id, mode, view) => `/audio/${id}?mode=${mode}&view=${view}`);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("AarReview", () => {
  it("distinguishes loading and failed session requests from an empty archive, and retries", async () => {
    let reject!: (reason: Error) => void;
    vi.mocked(api.sessions).mockReturnValueOnce(new Promise((_, fail) => { reject = fail; }));
    render(<AarReview timezone="UTC" />);
    expect(screen.getByText("Loading sessions…")).toBeInTheDocument();
    expect(screen.queryByText("No sessions recorded yet. Start a session in the Radios tab to begin recording.")).not.toBeInTheDocument();
    await act(async () => { reject(new Error("network")); });
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load sessions");
    expect(screen.queryByText("No sessions recorded yet. Start a session in the Radios tab to begin recording.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry sessions" }));
    expect(await screen.findByText("First message")).toBeInTheDocument();
    expect(api.sessions).toHaveBeenCalledTimes(2);
  });

  it("shows a real empty archive after a successful empty response", async () => {
    vi.mocked(api.sessions).mockResolvedValue([]);
    render(<AarReview timezone="UTC" />);
    expect(await screen.findByText("No sessions recorded yet. Start a session in the Radios tab to begin recording.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("distinguishes an expired instructor session from a network failure", async () => {
    vi.mocked(api.sessions).mockRejectedValue(new ApiError(401, "private detail"));
    render(<AarReview timezone="UTC" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Your instructor session has expired. Sign in again.");
    expect(screen.getByRole("alert")).not.toHaveTextContent("private detail");
  });

  it("shows failed transmission requests with a retry instead of claiming the session is empty", async () => {
    vi.mocked(api.events).mockRejectedValueOnce(new Error("offline"));
    render(<AarReview timezone="UTC" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load transmissions");
    expect(screen.queryByText("No transmissions in this session.")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry transmissions" }));
    expect(await screen.findByText("First message")).toBeInTheDocument();
  });

  it("combines callsign, frequency and confidence filters, and clears them", async () => {
    render(<AarReview timezone="UTC" />);
    await screen.findByText("First message");
    fireEvent.change(screen.getByLabelText("Filter by callsign"), { target: { value: "ALPHA" } });
    expect(screen.queryByText("Second message")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Filter by frequency"), { target: { value: "30.000" } });
    expect(screen.getByText("No transmissions match these filters.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    fireEvent.change(screen.getByLabelText("Filter by transcript confidence"), { target: { value: "high" } });
    expect(screen.queryByText("First message")).not.toBeInTheDocument();
    expect(screen.getByText("Second message")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.getByText("First message")).toBeInTheDocument();
  });

  it("plays only one recording, supplies native seek controls and switches render modes", async () => {
    render(<AarReview timezone="Pacific/Auckland" />);
    await screen.findByText("First message");
    fireEvent.click(screen.getByRole("button", { name: /Play recording from ALPHA/ }));
    let audio = screen.getByLabelText("Recording from ALPHA") as HTMLAudioElement;
    expect(audio.controls).toBe(true);
    expect(audio.getAttribute("src")).toBe("/audio/event-1?mode=dirty&view=cypher");
    fireEvent.change(screen.getByLabelText("Playback audio"), { target: { value: "clean" } });
    audio = screen.getByLabelText("Recording from ALPHA") as HTMLAudioElement;
    expect(audio.getAttribute("src")).toBe("/audio/event-1?mode=clean&view=cypher");
    expect(screen.getByLabelText("Playback receiver mode")).toBeDisabled();
    expect(screen.getByText(/Receiver mode applies to Dirty audio/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Playback audio"), { target: { value: "dirty" } });
    expect(screen.getByLabelText("Playback receiver mode")).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Playback receiver mode"), { target: { value: "plain" } });
    expect(screen.getByLabelText("Recording from ALPHA")).toHaveAttribute("src", "/audio/event-1?mode=dirty&view=plain");
    fireEvent.click(screen.getByRole("button", { name: /Play recording from BRAVO/ }));
    expect(screen.queryByLabelText("Recording from ALPHA")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Recording from BRAVO")).toBeInTheDocument();
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
    const firstRow = screen.getByText("First message").closest("article")!;
    expect(within(firstRow).getByText(/Oct/)).toHaveTextContent("21");
  });

  it("shows recording errors and stops playback when filters hide the recording", async () => {
    render(<AarReview timezone="UTC" />);
    await screen.findByText("First message");
    fireEvent.click(screen.getByRole("button", { name: /Play recording from ALPHA/ }));
    fireEvent.error(screen.getByLabelText("Recording from ALPHA"));
    expect(screen.getByRole("alert")).toHaveTextContent("Recording could not be played");
    fireEvent.click(screen.getByRole("button", { name: "Retry recording" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Filter by callsign"), { target: { value: "BRAVO" } });
    expect(screen.queryByLabelText("Recording from ALPHA")).not.toBeInTheDocument();
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
  });

  it("ignores late responses from a previously selected session", async () => {
    vi.mocked(api.sessions).mockResolvedValue([session, { ...session, id: "exercise-2", name: "Second exercise", started_at: "2026-10-19T12:00:00Z" }]);
    let resolve!: (rows: EventRow[]) => void;
    vi.mocked(api.events).mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    render(<AarReview timezone="UTC" />);
    await screen.findByRole("button", { name: /Second exercise/ });
    fireEvent.click(screen.getByRole("button", { name: /Second exercise/ }));
    await screen.findByText("First message");
    await act(async () => { resolve([event({ transcription: "Stale response" })]); });
    expect(screen.queryByText("Stale response")).not.toBeInTheDocument();
    expect(screen.getByText("First message")).toBeInTheDocument();
  });

  it("pauses the selected recording when switching sessions and unmounting", async () => {
    vi.mocked(api.sessions).mockResolvedValue([session, { ...session, id: "exercise-2", name: "Second exercise", started_at: "2026-10-19T12:00:00Z" }]);
    const { unmount } = render(<AarReview timezone="UTC" />);
    await screen.findByText("First message");
    fireEvent.click(screen.getByRole("button", { name: /Play recording from ALPHA/ }));
    const oldPlayer = screen.getByLabelText("Recording from ALPHA") as HTMLAudioElement;
    const oldPause = vi.spyOn(oldPlayer, "pause");
    fireEvent.click(screen.getByRole("button", { name: /Second exercise/ }));
    expect(oldPause).toHaveBeenCalled();
    await screen.findByText("First message");
    expect(screen.queryByLabelText("Recording from ALPHA")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Play recording from ALPHA/ }));
    const currentPause = vi.spyOn(screen.getByLabelText("Recording from ALPHA") as HTMLAudioElement, "pause");
    unmount();
    expect(currentPause).toHaveBeenCalled();
  });

  it("does not show a late export failure against another selected session", async () => {
    vi.mocked(api.sessions).mockResolvedValue([session, { ...session, id: "exercise-2", name: "Second exercise", started_at: "2026-10-19T12:00:00Z" }]);
    let reject!: (reason: Error) => void;
    vi.mocked(api.exportSession).mockReturnValueOnce(new Promise((_, fail) => { reject = fail; }));
    render(<AarReview timezone="UTC" />);
    await screen.findByText("First message");
    fireEvent.click(screen.getByRole("button", { name: "Export ZIP + audio" }));
    fireEvent.click(screen.getByRole("button", { name: /Second exercise/ }));
    await act(async () => { reject(new Error("offline")); });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("exports the selected session and reports failures without removing the review", async () => {
    vi.mocked(api.exportSession).mockRejectedValueOnce(new Error("offline"));
    render(<AarReview timezone="UTC" />);
    await screen.findByText("First message");
    fireEvent.click(screen.getByRole("button", { name: "Export ZIP + audio" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Export failed");
    expect(screen.getByText("First message")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Export ZIP + audio" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(api.exportSession).toHaveBeenLastCalledWith("exercise-1", "zip", "First exercise");
  });
});
