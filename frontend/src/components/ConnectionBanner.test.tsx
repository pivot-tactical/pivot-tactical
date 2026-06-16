import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
// Since vitest environment doesn't have expect globally exported by default in this setup,
// we'll explicitly extend expect
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

  it("renders offline banner when state is offline", () => {
    render(<ConnectionBanner state="offline" />);
    const banner = screen.getByRole("status");
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveTextContent("Connection to the server lost — reconnecting…");
    expect(banner).toHaveStyle({ background: "#e08020" });
  });

  it("renders restarting banner when state is restarting", () => {
    render(<ConnectionBanner state="restarting" />);
    const banner = screen.getByRole("status");
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveTextContent("PIVOT is restarting — reconnecting, the page will refresh automatically…");
    expect(banner).toHaveStyle({ background: "#f0c000" });
  });
});
