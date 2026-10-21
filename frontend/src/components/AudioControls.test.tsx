import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AudioIO, MicState } from "../audio";
import { AudioControls } from "./AudioControls";

function mockAudio() {
  let state: MicState = { status: "error", deviceId: "", error: "Allow microphone access in your browser, then retry." };
  const listeners = new Set<() => void>();
  const audio = {
    getMicState: () => state,
    onMicState: (listener: () => void) => { listeners.add(listener); return () => listeners.delete(listener); },
    getInputLevel: vi.fn(() => 0.4),
    prewarm: vi.fn(async () => {
      state = { ...state, status: "ready", error: null };
      listeners.forEach((listener) => listener());
    }),
    setInputDevice: vi.fn().mockResolvedValue(undefined),
  };
  return audio;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("AudioControls", () => {
  it("shows actionable permission errors and updates status after retry", async () => {
    const audio = mockAudio();
    render(<AudioControls audio={audio as unknown as AudioIO} />);
    expect(screen.getByText(/Allow microphone access/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry microphone" }));
    await waitFor(() => expect(screen.getByText("Microphone ready")).toBeTruthy());
    expect(screen.queryByText(/Allow microphone access/)).toBeNull();
  });

  it("lists microphones, selects a device, and shows the live input level", async () => {
    const audio = mockAudio();
    vi.stubGlobal("navigator", { mediaDevices: {
      enumerateDevices: vi.fn().mockResolvedValue([{ kind: "audioinput", deviceId: "headset", label: "USB headset" }]),
      addEventListener: vi.fn(), removeEventListener: vi.fn(),
    } });
    render(<AudioControls audio={audio as unknown as AudioIO} />);
    await screen.findByRole("option", { name: "USB headset" });
    fireEvent.change(screen.getByLabelText("Microphone device"), { target: { value: "headset" } });
    expect(audio.setInputDevice).toHaveBeenCalledWith("headset");
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 100)); });
    expect((screen.getByLabelText("Microphone input level") as HTMLMeterElement).value).toBe(0.4);
  });
});
