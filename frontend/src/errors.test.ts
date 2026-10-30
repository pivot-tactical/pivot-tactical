import { describe, expect, it, vi, afterEach } from "vitest";
import { api, ApiError } from "./api";
import { errorMessage, isAuthenticationError } from "./errors";

afterEach(() => vi.unstubAllGlobals());

describe("HTTP errors", () => {
  it("retains HTTP status from the REST client", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 401, text: async () => "Unauthorized" }));
    await expect(api.loginInstructor("wrong")).rejects.toMatchObject({ status: 401, name: "ApiError" });
  });

  it("distinguishes authentication, server, and network failures without exposing details", () => {
    expect(isAuthenticationError(new ApiError(401, "internal"))).toBe(true);
    expect(isAuthenticationError(new TypeError("Failed to fetch"))).toBe(false);
    expect(errorMessage(new ApiError(500, "private details"))).toContain("server could not complete");
    expect(errorMessage(new TypeError("Failed to fetch"))).toContain("Cannot reach the server");
    expect(errorMessage(new ApiError(429, "private details"))).toContain("Too many attempts");
  });
});
