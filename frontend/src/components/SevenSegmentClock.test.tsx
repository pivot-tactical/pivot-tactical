import { describe, it, expect, afterEach, vi, beforeEach } from "vitest";
import { render, screen, cleanup, act } from "@testing-library/react";
import * as matchers from "@testing-library/jest-dom/matchers";
expect.extend(matchers);

import { SevenSegmentClock } from "./SevenSegmentClock";

describe("SevenSegmentClock", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("renders correctly with a valid timezone", () => {
    const date = new Date("2024-01-01T12:34:56Z");
    vi.setSystemTime(date);

    render(<SevenSegmentClock timezone="UTC" />);

    expect(screen.getByText("12:34:56")).toBeInTheDocument();
    expect(screen.getByText("UTC")).toBeInTheDocument();
  });

  it("handles Intl.DateTimeFormat error and falls back to ISO string", () => {
    const date = new Date("2024-01-01T12:34:56Z");
    vi.setSystemTime(date);

    const spy = vi.spyOn(Intl, "DateTimeFormat").mockImplementation(() => {
      throw new Error("mock error");
    });

    render(<SevenSegmentClock timezone="UTC" />);

    expect(screen.getByText("12:34:56")).toBeInTheDocument();
    expect(screen.getByText("UTC")).toBeInTheDocument();

    spy.mockRestore();
  });

  it("updates time on interval", () => {
    const date = new Date("2024-01-01T12:34:56Z");
    vi.setSystemTime(date);

    render(<SevenSegmentClock timezone="UTC" />);
    expect(screen.getByText("12:34:56")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(1000);
    });

    expect(screen.getByText("12:34:57")).toBeInTheDocument();
  });
});
