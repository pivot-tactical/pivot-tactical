import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import * as matchers from "@testing-library/jest-dom/matchers";
expect.extend(matchers);

import { VolumeSlider } from "./VolumeSlider";

describe("VolumeSlider", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders with correct initial percentage", () => {
    render(<VolumeSlider value={0.5} onChange={() => {}} />);

    expect(screen.getByText("VOLUME · 50%")).toBeInTheDocument();

    const slider = screen.getByRole("slider", { name: "Headset volume" });
    expect(slider).toBeInTheDocument();
    expect(slider).toHaveValue("50");
  });

  it("calls onChange with correct fractional value when slider changes", () => {
    const handleChange = vi.fn();
    render(<VolumeSlider value={0.5} onChange={handleChange} />);

    const slider = screen.getByRole("slider", { name: "Headset volume" });

    fireEvent.change(slider, { target: { value: "75" } });

    expect(handleChange).toHaveBeenCalledTimes(1);
    expect(handleChange).toHaveBeenCalledWith(0.75);
  });

  it("renders disabled state correctly", () => {
    render(<VolumeSlider value={0.5} onChange={() => {}} disabled={true} />);

    const slider = screen.getByRole("slider", { name: "Headset volume" });
    expect(slider).toBeDisabled();
  });

  it("handles minimum value edge case (0)", () => {
    render(<VolumeSlider value={0} onChange={() => {}} />);

    expect(screen.getByText("VOLUME · 0%")).toBeInTheDocument();

    const slider = screen.getByRole("slider", { name: "Headset volume" });
    expect(slider).toHaveValue("0");
  });

  it("handles maximum value edge case (1)", () => {
    render(<VolumeSlider value={1} onChange={() => {}} />);

    expect(screen.getByText("VOLUME · 100%")).toBeInTheDocument();

    const slider = screen.getByRole("slider", { name: "Headset volume" });
    expect(slider).toHaveValue("100");
  });
});
