import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import * as matchers from "@testing-library/jest-dom/matchers";
expect.extend(matchers);

import { ConnectionBanner } from "./ConnectionBanner";

describe("ConnectionBanner", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders nothing when state is online", () => {
    const { container } = render(<ConnectionBanner state="online" />);
    expect(container.firstChild).toBeNull();
  });

  it("renders offline banner with correct styles and text", () => {
    render(<ConnectionBanner state="offline" />);
    const banner = screen.getByRole("status");
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveTextContent("Connection to the server lost — reconnecting…");
    expect(banner).toHaveStyle({
      position: "fixed",
      top: "0px",
      left: "0px",
      right: "0px",
      zIndex: "1000",
      textAlign: "center",
      padding: "6px 12px",
      color: "#000",
      background: "#e08020",
    });
  });

  it("renders restarting banner with correct styles and text", () => {
    render(<ConnectionBanner state="restarting" />);
    const banner = screen.getByRole("status");
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveTextContent("PIVOT is restarting — reconnecting, the page will refresh automatically…");
    expect(banner).toHaveStyle({
      position: "fixed",
      top: "0px",
      left: "0px",
      right: "0px",
      zIndex: "1000",
      textAlign: "center",
      padding: "6px 12px",
      color: "#000",
      background: "#f0c000",
    });
  });
});
