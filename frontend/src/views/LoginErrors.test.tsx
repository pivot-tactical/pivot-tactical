import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Login } from "./Login";
import { api } from "../api";
import { ApiError } from "../errors";

vi.mock("../api", () => ({ api: { status: vi.fn() } }));

describe("login error recovery", () => {
  beforeEach(() => {
    vi.mocked(api.status).mockResolvedValue({} as never);
  });
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  it("reports trainee join failure, preserves the callsign, and retries", async () => {
    const join = vi.fn().mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValueOnce(undefined);
    render(<Login onTrainee={join} onInstructor={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText("e.g. ALPHA-1"), { target: { value: "ALPHA-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Join Net" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the server");
    expect(screen.getByPlaceholderText("e.g. ALPHA-1")).toHaveValue("ALPHA-1");
    fireEvent.click(screen.getByRole("button", { name: "Join Net" }));
    await waitFor(() => expect(join).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not label a server failure as an incorrect password", async () => {
    const signIn = vi.fn().mockRejectedValue(new ApiError(503, "Internal details"));
    render(<Login onTrainee={vi.fn()} onInstructor={signIn} />);
    fireEvent.click(screen.getByRole("button", { name: /Log in as instructor/ }));
    fireEvent.change(screen.getByPlaceholderText("default: instructor"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign In" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("server could not complete");
    expect(screen.queryByText("Incorrect password.")).not.toBeInTheDocument();
    expect(screen.queryByText(/Internal details/)).not.toBeInTheDocument();
  });

  it("prevents repeat Enter submissions while instructor sign in is pending", async () => {
    let resolve!: () => void;
    const signIn = vi.fn().mockReturnValue(new Promise<void>((r) => { resolve = r; }));
    render(<Login onTrainee={vi.fn()} onInstructor={signIn} />);
    fireEvent.click(screen.getByRole("button", { name: /Log in as instructor/ }));
    const password = screen.getByPlaceholderText("default: instructor");
    fireEvent.change(password, { target: { value: "secret" } });
    fireEvent.keyDown(password, { key: "Enter" });
    fireEvent.keyDown(password, { key: "Enter" });
    expect(signIn).toHaveBeenCalledTimes(1);
    resolve();
    await waitFor(() => expect(screen.getByRole("button", { name: "Sign In" })).toBeEnabled());
  });

  it("retries server status without reloading the page", async () => {
    vi.mocked(api.status).mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValueOnce({} as never);
    render(<Login onTrainee={vi.fn()} onInstructor={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry connection" }));
    expect(await screen.findByText("Server online")).toBeInTheDocument();
  });
});
