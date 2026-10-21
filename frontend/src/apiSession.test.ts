import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, getToken, setToken } from "./api";

const ok = (body: unknown) => ({ ok: true, json: async () => body });

describe("instructor cookie session lifecycle", () => {
  beforeEach(() => setToken(null));
  afterEach(() => { setToken(null); vi.unstubAllGlobals(); });

  it("remembers successful cookie login even when the JSON token is null", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok({ role: "instructor", token: null })));
    await api.loginInstructor("secret");
    expect(getToken()).toBe("cookie");
    expect(sessionStorage.getItem("pivot_instructor")).toBe("1");
  });

  it("keeps the session flag after a failed logout and clears it after retry succeeds", async () => {
    setToken("cookie");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(ok({ ok: true })));
    await expect(api.logout()).rejects.toThrow("Failed to fetch");
    expect(getToken()).toBe("cookie");
    await api.logout();
    expect(getToken()).toBeNull();
  });

  it("cancels old refreshes and does not restore the flag after logout", async () => {
    let resolveRefresh!: (response: unknown) => void;
    const fetchMock = vi.fn().mockReturnValueOnce(new Promise((resolve) => { resolveRefresh = resolve; }))
      .mockResolvedValueOnce(ok({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    setToken("cookie");
    const refresh = api.refreshToken();
    const signal = fetchMock.mock.calls[0][1].signal as AbortSignal;
    await api.logout();
    expect(signal.aborted).toBe(true);
    // Even if a response was already received when abort ran, it cannot mark
    // the newly signed-out tab as an instructor again.
    resolveRefresh(ok({ token: null }));
    await refresh;
    expect(getToken()).toBeNull();
  });
});
