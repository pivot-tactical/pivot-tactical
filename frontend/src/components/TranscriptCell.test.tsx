import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import * as matchers from "@testing-library/jest-dom/matchers";
expect.extend(matchers);

import { TranscriptCell } from "./TranscriptCell";
import type { EventRow } from "../types";
import { api } from "../api";

vi.mock("../api", () => ({
  api: {
    editTranscription: vi.fn(),
  },
}));

describe("TranscriptCell", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  const baseEvent: EventRow = {
    event_id: "ev-101",
    trainee_name: "ALPHA",
    frequency: "142.0000 MHz",
    band_region: "VHF High",
    tx_mode: "Plain",
    audibility: "LOUD-CLEAR",
    sync_status: "Completed",
    jammed: false,
    snr_db: 20,
    timestamp_start: "2025-01-01T12:00:00Z",
    duration_ms: 1500,
    transcription_status: "Settled",
    transcription: "Roger Wilco",
    transcription_confidence: 0.95,
    transcription_edited: false,
    transcription_original: null,
  };

  it("renders pending transcription status as non-editable plain text", () => {
    const ev: EventRow = {
      ...baseEvent,
      transcription_status: "Pending",
      transcription: null,
    };
    const onEventUpdate = vi.fn();

    render(<TranscriptCell ev={ev} onEventUpdate={onEventUpdate} />);

    expect(screen.getByText("transcribing…")).toBeInTheDocument();
    const cell = screen.getByText("transcribing…");
    fireEvent.click(cell);

    // Should not enter editing mode
    expect(screen.queryByPlaceholderText("Type what was said…")).not.toBeInTheDocument();
  });

  it("renders low-confidence transcription with text--amber class", () => {
    const ev: EventRow = {
      ...baseEvent,
      transcription_confidence: 0.6,
    };
    const onEventUpdate = vi.fn();

    render(<TranscriptCell ev={ev} onEventUpdate={onEventUpdate} />);

    const cell = screen.getByTitle("Click to edit the transcript");
    expect(cell).toHaveClass("text--amber");
  });

  it("renders empty transcription as dash '—'", () => {
    const ev: EventRow = {
      ...baseEvent,
      transcription: null,
    };
    const onEventUpdate = vi.fn();

    render(<TranscriptCell ev={ev} onEventUpdate={onEventUpdate} />);

    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("opens edit textarea when clicked and reverts on Cancel or Escape", () => {
    const onEventUpdate = vi.fn();
    render(<TranscriptCell ev={baseEvent} onEventUpdate={onEventUpdate} />);

    const cell = screen.getByTitle("Click to edit the transcript");
    fireEvent.click(cell);

    const textarea = screen.getByPlaceholderText("Type what was said…");
    expect(textarea).toBeInTheDocument();
    expect(textarea).toHaveValue("Roger Wilco");

    // Press Escape to cancel
    fireEvent.keyDown(textarea, { key: "Escape" });
    expect(screen.queryByPlaceholderText("Type what was said…")).not.toBeInTheDocument();

    // Reopen and test Cancel button
    fireEvent.click(screen.getByTitle("Click to edit the transcript"));
    expect(screen.getByPlaceholderText("Type what was said…")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByPlaceholderText("Type what was said…")).not.toBeInTheDocument();
  });

  it("submits updated transcription on Save click", async () => {
    const onEventUpdate = vi.fn();
    const updatedEv = { ...baseEvent, transcription: "Roger Wilco Over", transcription_edited: true };
    vi.mocked(api.editTranscription).mockResolvedValue(updatedEv);

    render(<TranscriptCell ev={baseEvent} onEventUpdate={onEventUpdate} />);

    fireEvent.click(screen.getByTitle("Click to edit the transcript"));
    const textarea = screen.getByPlaceholderText("Type what was said…");
    fireEvent.change(textarea, { target: { value: "Roger Wilco Over" } });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(api.editTranscription).toHaveBeenCalledWith("ev-101", "Roger Wilco Over");
    await waitFor(() => {
      expect(onEventUpdate).toHaveBeenCalledWith(updatedEv);
      expect(screen.queryByPlaceholderText("Type what was said…")).not.toBeInTheDocument();
    });
  });

  it("submits updated transcription on Enter key without shiftKey", async () => {
    const onEventUpdate = vi.fn();
    const updatedEv = { ...baseEvent, transcription: "Roger Wilco Out", transcription_edited: true };
    vi.mocked(api.editTranscription).mockResolvedValue(updatedEv);

    render(<TranscriptCell ev={baseEvent} onEventUpdate={onEventUpdate} />);

    fireEvent.click(screen.getByTitle("Click to edit the transcript"));
    const textarea = screen.getByPlaceholderText("Type what was said…");
    fireEvent.change(textarea, { target: { value: "Roger Wilco Out" } });

    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });

    expect(api.editTranscription).toHaveBeenCalledWith("ev-101", "Roger Wilco Out");
    await waitFor(() => {
      expect(onEventUpdate).toHaveBeenCalledWith(updatedEv);
    });
  });

  it("closes edit mode without calling API on no-op edit", async () => {
    const onEventUpdate = vi.fn();
    render(<TranscriptCell ev={baseEvent} onEventUpdate={onEventUpdate} />);

    fireEvent.click(screen.getByTitle("Click to edit the transcript"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(api.editTranscription).not.toHaveBeenCalled();
    expect(screen.queryByPlaceholderText("Type what was said…")).not.toBeInTheDocument();
  });

  it("renders edited transcript diff highlighting and edited badge", () => {
    const ev: EventRow = {
      ...baseEvent,
      transcription_original: "Roger Wilco",
      transcription: "Roger Wilco Over",
      transcription_edited: true,
    };
    const onEventUpdate = vi.fn();

    render(<TranscriptCell ev={ev} onEventUpdate={onEventUpdate} />);

    expect(screen.getByText("✎ edited")).toBeInTheDocument();
    // "Over" was inserted so it should be inside a mark element
    const editMark = screen.getByText("Over");
    expect(editMark.tagName.toLowerCase()).toBe("mark");
    expect(editMark).toHaveClass("transcript__edit");
  });

  it("renders jammed badge when event is jammed", () => {
    const ev: EventRow = {
      ...baseEvent,
      jammed: true,
      snr_db: -5.2,
    };
    const onEventUpdate = vi.fn();

    render(<TranscriptCell ev={ev} onEventUpdate={onEventUpdate} />);

    expect(screen.getByText("JAMMED")).toBeInTheDocument();
  });
});
