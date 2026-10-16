import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
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

  it("handles channel jam toggle and interference changes", () => {
    const props = createProps();
    render(<InstrRadioCard {...props} />);

    const jamButton = screen.getByRole("button", { name: "Jam INSTRUCTOR (Alpha) channel" });
    fireEvent.click(jamButton);
    expect(api.scenario).toHaveBeenCalledWith({
      net_scenario: { frequency_hz: 142000000, jammed: true },
    });
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
    fireEvent.mouseDown(pttButton);
    expect(props.onStart).toHaveBeenCalledWith(mockRadio);

    fireEvent.mouseUp(pttButton);
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
