import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent, act } from "@testing-library/react";
import * as matchers from "@testing-library/jest-dom/matchers";
expect.extend(matchers);

import { InstrRadioCard } from "./InstrRadioCard";
import type { RadioState, NetScenario, TxPhase } from "../types";
import { AudioIO } from "../audio";
import { api } from "../api";

vi.mock("../api", () => ({
  api: {
    scenario: vi.fn().mockResolvedValue({}),
  },
}));

describe("InstrRadioCard", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  const mockRadio: RadioState = {
    radio_id: "radio-1",
    name: "INSTRUCTOR (Alpha)",
    is_instructor: true,
    frequency: "142.0000 MHz",
    frequency_hz: 142000000,
    status: "IDLE",
    mode: "Plain",
    rx_noise: true,
    band_region: "VHF High",
  };

  const createProps = (overrides = {}) => {
    const audioMock = {
      setVolume: vi.fn(),
    } as unknown as AudioIO;

    const rxLevelsMock = { current: { "radio-1": 0.8 } };

    return {
      radio: mockRadio,
      index: 1,
      socket: {
        instrTune: vi.fn(),
        instrMode: vi.fn(),
        instrRxNoise: vi.fn(),
      } as any,
      audio: audioMock,
      phase: "IDLE" as TxPhase,
      scenario: { freq_hz: 142000000, interference: 0, jammed: false } as NetScenario,
      rxLevels: rxLevelsMock,
      onStart: vi.fn(),
      onEnd: vi.fn(),
      onRemove: vi.fn(),
      ...overrides,
    };
  };

  it("renders radio card info, name, frequency, and controls correctly", () => {
    const props = createProps();
    render(<InstrRadioCard {...props} />);

    expect(screen.getByText("INSTRUCTOR (Alpha)")).toBeInTheDocument();
    expect(screen.getByText("142.0000")).toBeInTheDocument();
    expect(screen.getByLabelText("Push to talk on INSTRUCTOR (Alpha)")).toBeInTheDocument();
    expect(screen.getByText("HOLD · NUMPAD 1")).toBeInTheDocument();
  });

  it("allows frequency tuning via step buttons and text input", () => {
    const props = createProps();
    render(<InstrRadioCard {...props} />);

    const stepUpButton = screen.getByLabelText("Increase frequency on INSTRUCTOR (Alpha)");
    fireEvent.click(stepUpButton);
    expect(props.socket.instrTune).toHaveBeenLastCalledWith("radio-1", "142.0125 MHz");

    const input = screen.getByLabelText("Frequency in MHz on INSTRUCTOR (Alpha)");
    fireEvent.change(input, { target: { value: "145.500" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(props.socket.instrTune).toHaveBeenLastCalledWith("radio-1", "145.5000 MHz");
  });

  it("handles mode dial toggling", () => {
    const props = createProps();
    render(<InstrRadioCard {...props} />);

    const modeDial = screen.getByTitle("Plain / Cypher on INSTRUCTOR (Alpha) (persists across retuning)");
    fireEvent.click(modeDial);
    expect(props.socket.instrMode).toHaveBeenCalledWith("radio-1", "Cypher");
  });

  it("handles channel jam toggle and interference changes", async () => {
    const props = createProps();
    render(<InstrRadioCard {...props} />);

    const jamButton = screen.getByRole("button", { name: "Jam INSTRUCTOR (Alpha) channel" });
    await act(async () => { fireEvent.click(jamButton); });
    expect(api.scenario).toHaveBeenCalledWith({
      net_scenario: { frequency_hz: 142000000, jammed: true },
    });
  });

  it("reports failed noise changes, restores the confirmed level, and retries the same change", async () => {
    vi.mocked(api.scenario).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const props = createProps();
    render(<InstrRadioCard {...props} />);
    const slider = screen.getByRole("slider", { name: "Noise offset on INSTRUCTOR (Alpha) channel (0 = natural baseline)" });
    await act(async () => { fireEvent.change(slider, { target: { value: "70" } }); });
    expect(slider).toHaveValue("0");
    expect(screen.getByRole("alert")).toHaveTextContent("Cannot reach the server");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Retry channel change" })); });
    expect(api.scenario).toHaveBeenCalledTimes(2);
    expect(api.scenario).toHaveBeenLastCalledWith({ net_scenario: { frequency_hz: 142000000, interference: 0.7 } });
    expect(slider).toHaveValue("70");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("ignores a delayed failure from a channel the instructor has left", async () => {
    let reject!: (error: Error) => void;
    vi.mocked(api.scenario).mockReturnValueOnce(new Promise((_resolve, fail) => { reject = fail; }));
    const props = createProps();
    const { rerender } = render(<InstrRadioCard {...props} />);
    const slider = screen.getByRole("slider", { name: "Noise offset on INSTRUCTOR (Alpha) channel (0 = natural baseline)" });
    fireEvent.change(slider, { target: { value: "70" } });
    expect(slider).toBeDisabled();
    rerender(<InstrRadioCard {...props}
      radio={{ ...mockRadio, frequency: "145.0000 MHz", frequency_hz: 145000000 }}
      scenario={{ freq_hz: 145000000, interference: 0.25, jammed: false }} />);
    expect(slider).toHaveValue("25");
    expect(slider).not.toBeDisabled();
    await act(async () => reject(new TypeError("Failed to fetch")));
    expect(slider).toHaveValue("25");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry channel change" })).not.toBeInTheDocument();
  });

  it("disables channel changes when the connection or console controls are unavailable", () => {
    const props = createProps({ controlsDisabled: true });
    render(<InstrRadioCard {...props} />);
    expect(screen.getByRole("slider", { name: "Noise offset on INSTRUCTOR (Alpha) channel (0 = natural baseline)" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Jam INSTRUCTOR (Alpha) channel" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Jam INSTRUCTOR (Alpha) channel" }));
    expect(api.scenario).not.toHaveBeenCalled();
  });

  it("handles RX noise toggle button", () => {
    const props = createProps();
    render(<InstrRadioCard {...props} />);

    const rxNoiseButton = screen.getByLabelText("Toggle RX Noise on INSTRUCTOR (Alpha)");
    expect(rxNoiseButton).toHaveTextContent("RX Noise: On");

    fireEvent.click(rxNoiseButton);
    expect(props.socket.instrRxNoise).toHaveBeenCalledWith("radio-1", false);
  });

  it("triggers PTT callbacks onStart and onEnd", () => {
    const props = createProps();
    render(<InstrRadioCard {...props} />);

    const pttButton = screen.getByLabelText("Push to talk on INSTRUCTOR (Alpha)");
    fireEvent(pttButton, new PointerEvent("pointerdown", { bubbles: true, button: 0, pointerId: 1 }));
    expect(props.onStart).toHaveBeenCalledWith(mockRadio);

    fireEvent(pttButton, new PointerEvent("pointerup", { bubbles: true, button: 0, pointerId: 1 }));
    expect(props.onEnd).toHaveBeenCalledWith(mockRadio);
  });

  it("calls onRemove when remove button is clicked", () => {
    const props = createProps();
    render(<InstrRadioCard {...props} />);

    const removeButton = screen.getByLabelText("Remove radio INSTRUCTOR (Alpha)");
    fireEvent.click(removeButton);
    expect(props.onRemove).toHaveBeenCalledWith("radio-1");
  });
});
