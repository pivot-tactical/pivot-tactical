import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Login } from "./Login";
import { api } from "../api";
import * as matchers from "@testing-library/jest-dom/matchers";

expect.extend(matchers);

// Mock the API to avoid actual network calls
vi.mock("../api", () => ({
  api: {
    status: vi.fn(),
  },
}));

describe("Login", () => {
  const mockOnTrainee = vi.fn();
  const mockOnInstructor = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    (api.status as any).mockResolvedValue({
      name: "PIVOT",
      version: "1.0.0",
      session_active: false,
      terminals: 0,
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders trainee mode by default", () => {
    render(<Login onTrainee={mockOnTrainee} onInstructor={mockOnInstructor} />);
    expect(screen.getByText("PIVOT")).toBeInTheDocument();
    expect(screen.getByText("Name / Callsign")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("e.g. ALPHA-1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Join Net/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Log in as instructor/i })).toBeInTheDocument();
  });

  it("disables the Join Net button for invalid callsigns", async () => {
    const user = userEvent.setup();
    render(<Login onTrainee={mockOnTrainee} onInstructor={mockOnInstructor} />);

    const input = screen.getByPlaceholderText("e.g. ALPHA-1");
    const joinBtn = screen.getByRole("button", { name: /Join Net/i });

    expect(joinBtn).toBeDisabled();

    await user.type(input, "A@!");
    expect(joinBtn).toBeDisabled();

    await user.clear(input);
    await user.type(input, "ALPHA-1");
    expect(joinBtn).not.toBeDisabled();
  });

  it("submits trainee login on button click", async () => {
    const user = userEvent.setup();
    render(<Login onTrainee={mockOnTrainee} onInstructor={mockOnInstructor} />);

    const input = screen.getByPlaceholderText("e.g. ALPHA-1");
    const joinBtn = screen.getByRole("button", { name: /Join Net/i });

    await user.type(input, "BRAVO-2");
    await user.click(joinBtn);

    expect(mockOnTrainee).toHaveBeenCalledWith("BRAVO-2");
  });

  it("submits trainee login on Enter key", async () => {
    const user = userEvent.setup();
    render(<Login onTrainee={mockOnTrainee} onInstructor={mockOnInstructor} />);

    const input = screen.getByPlaceholderText("e.g. ALPHA-1");
    await user.type(input, "CHARLIE-3{enter}");

    expect(mockOnTrainee).toHaveBeenCalledWith("CHARLIE-3");
  });

  it("switches to instructor mode and back", async () => {
    const user = userEvent.setup();
    render(<Login onTrainee={mockOnTrainee} onInstructor={mockOnInstructor} />);

    const toInstructorBtn = screen.getByRole("button", { name: /Log in as instructor/i });
    await user.click(toInstructorBtn);

    expect(screen.getByText("Instructor Password")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("default: instructor")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Sign In/i })).toBeInTheDocument();

    const toTraineeBtn = screen.getByRole("button", { name: /Back to trainee login/i });
    await user.click(toTraineeBtn);

    expect(screen.getByText("Name / Callsign")).toBeInTheDocument();
  });

  it("submits instructor login and handles errors", async () => {
    const user = userEvent.setup();
    render(<Login onTrainee={mockOnTrainee} onInstructor={mockOnInstructor} />);

    // Switch to instructor
    await user.click(screen.getByRole("button", { name: /Log in as instructor/i }));

    const input = screen.getByPlaceholderText("default: instructor");
    const signInBtn = screen.getByRole("button", { name: /Sign In/i });

    expect(signInBtn).toBeDisabled();

    // Type password
    await user.type(input, "wrong-password");
    expect(signInBtn).not.toBeDisabled();

    // Mock failure
    mockOnInstructor.mockRejectedValueOnce(new Error("Unauthorized"));

    await user.click(signInBtn);

    // Should display error
    await waitFor(() => {
      expect(screen.getByText("Incorrect password.")).toBeInTheDocument();
    });

    // Submitting again clears the error and shows busy
    mockOnInstructor.mockResolvedValueOnce(undefined);
    await user.click(signInBtn);
    await waitFor(() => {
      expect(screen.queryByText("Incorrect password.")).not.toBeInTheDocument();
    });
    expect(mockOnInstructor).toHaveBeenNthCalledWith(2, "wrong-password");
  });

  it("submits instructor login on Enter key", async () => {
    const user = userEvent.setup();
    render(<Login onTrainee={mockOnTrainee} onInstructor={mockOnInstructor} />);

    // Switch to instructor
    await user.click(screen.getByRole("button", { name: /Log in as instructor/i }));

    const input = screen.getByPlaceholderText("default: instructor");
    await user.type(input, "my-password{enter}");

    await waitFor(() => {
      expect(mockOnInstructor).toHaveBeenCalledWith("my-password");
    });
  });

  it("submits instructor login on Enter key and handles errors", async () => {
    const user = userEvent.setup();
    render(<Login onTrainee={mockOnTrainee} onInstructor={mockOnInstructor} />);

    // Switch to instructor
    await user.click(screen.getByRole("button", { name: /Log in as instructor/i }));

    const input = screen.getByPlaceholderText("default: instructor");

    // Mock failure
    mockOnInstructor.mockRejectedValueOnce(new Error("Unauthorized"));

    await user.type(input, "wrong-password{enter}");

    // Should display error
    await waitFor(() => {
      expect(screen.getByText("Incorrect password.")).toBeInTheDocument();
    });
  });

  it("does not submit instructor login on Enter key when password is empty", async () => {
    const user = userEvent.setup();
    render(<Login onTrainee={mockOnTrainee} onInstructor={mockOnInstructor} />);

    await user.click(screen.getByRole("button", { name: /Log in as instructor/i }));

    // Press Enter without typing a password
    const input = screen.getByPlaceholderText("default: instructor");
    await user.type(input, "{enter}");

    expect(mockOnInstructor).not.toHaveBeenCalled();
  });

  describe("microphone checks", () => {
    let originalIsSecureContext: boolean;
    let originalMediaDevices: any;

    beforeEach(() => {
      originalIsSecureContext = window.isSecureContext;
      originalMediaDevices = navigator.mediaDevices;
    });

    afterEach(() => {
      Object.defineProperty(window, "isSecureContext", { value: originalIsSecureContext, configurable: true });
      Object.defineProperty(navigator, "mediaDevices", { value: originalMediaDevices, configurable: true });
    });

    it("displays blocked message for insecure contexts", async () => {
      const user = userEvent.setup();

      // Mock insecure context
      Object.defineProperty(window, "isSecureContext", { value: false, configurable: true });

      render(<Login onTrainee={mockOnTrainee} onInstructor={mockOnInstructor} />);

      const checkMicBtn = screen.getByRole("button", { name: /Check microphone/i });
      await user.click(checkMicBtn);

      expect(screen.getByText(/Microphone blocked/i)).toBeInTheDocument();
      expect(screen.getByText(/This connection isn't secure/i)).toBeInTheDocument();
    });

    it("checks microphone successfully in secure context", async () => {
      const user = userEvent.setup();

      // Mock secure context and getUserMedia
      Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });

      const mockStream = {
        getTracks: () => [{ stop: vi.fn() }],
      };

      Object.defineProperty(navigator, "mediaDevices", {
        value: {
          getUserMedia: vi.fn().mockResolvedValue(mockStream),
        },
        configurable: true,
      });

      render(<Login onTrainee={mockOnTrainee} onInstructor={mockOnInstructor} />);

      const checkMicBtn = screen.getByRole("button", { name: /Check microphone/i });
      await user.click(checkMicBtn);

      await waitFor(() => {
        expect(screen.getByText(/Microphone OK/i)).toBeInTheDocument();
      });
    });

    it("shows allow-access hint when getUserMedia is denied", async () => {
      const user = userEvent.setup();

      Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });

      Object.defineProperty(navigator, "mediaDevices", {
        value: {
          getUserMedia: vi.fn().mockRejectedValueOnce(new Error("NotAllowedError")),
        },
        configurable: true,
      });

      render(<Login onTrainee={mockOnTrainee} onInstructor={mockOnInstructor} />);

      const checkMicBtn = screen.getByRole("button", { name: /Check microphone/i });
      await user.click(checkMicBtn);

      await waitFor(() => {
        expect(screen.getByText(/Microphone blocked/i)).toBeInTheDocument();
        expect(screen.getByText(/Allow microphone access in your browser to transmit./i)).toBeInTheDocument();
      });
    });
  });

  it("shows server status dot correctly based on API status", async () => {
    render(<Login onTrainee={mockOnTrainee} onInstructor={mockOnInstructor} />);

    expect(screen.getByText("Connecting…")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("Server online")).toBeInTheDocument();
    });
  });

  it("shows server unreachable status dot when API rejects", async () => {
    (api.status as any).mockRejectedValueOnce(new Error("Network Error"));

    render(<Login onTrainee={mockOnTrainee} onInstructor={mockOnInstructor} />);

    expect(screen.getByText("Connecting…")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("Server unreachable")).toBeInTheDocument();
    });
  });
});
