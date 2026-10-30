import { act, cleanup, fireEvent, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AudioIO } from "./audio";
import type { PivotSocket } from "./ws";
import { usePtt } from "./usePtt";

vi.mock("./audio", () => ({ playClick: vi.fn(), playSyncTone: vi.fn() }));

function setup(instructor = false) {
  const listeners = new Map<string, Set<(p: any) => void>>();
  const socket = {
    connected: true,
    on: vi.fn((type: string, callback: (p: any) => void) => {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type)!.add(callback);
      return () => listeners.get(type)?.delete(callback);
    }),
    pttStart: vi.fn(), pttEnd: vi.fn(), pttAbort: vi.fn(),
    instrPttStart: vi.fn(), instrPttEnd: vi.fn(), instrPttAbort: vi.fn(), sendAudio: vi.fn(),
  };
  const audio = { startCapture: vi.fn().mockResolvedValue(undefined), stopCapture: vi.fn() };
  const radios = [
    { id: "one", frequency: "7.0000 MHz", mode: "Plain" as const },
    { id: "two", frequency: "14.0000 MHz", mode: "Cypher" as const },
  ];
  const props = { audio: audio as unknown as AudioIO, socket: socket as unknown as PivotSocket, radios, enabled: true, instructor };
  const hook = renderHook((options) => usePtt(options), { initialProps: props });
  const emit = (type: string, payload: any = {}) => act(() => {
    listeners.get(type)?.forEach((listener) => listener(payload));
  });
  return { ...hook, audio, socket, radios, props, emit };
}

describe("usePtt safeguards", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  it("does not key the net or leave capture running when microphone opening fails", async () => {
    const { result, audio, socket } = setup();
    audio.startCapture.mockRejectedValueOnce(new Error("Allow microphone access to transmit."));
    await act(async () => result.current.start("one"));
    expect(socket.pttStart).not.toHaveBeenCalled();
    expect(audio.stopCapture).toHaveBeenCalled();
    expect(result.current.error).toContain("Allow microphone");
  });

  it("cancels a release made while the microphone permission request is pending", async () => {
    const { result, audio, socket } = setup();
    let resolve!: () => void;
    audio.startCapture.mockImplementationOnce(() => new Promise<void>((done) => { resolve = done; }));
    let pending!: Promise<void>;
    act(() => { pending = result.current.start("one"); });
    act(() => result.current.end("one"));
    await act(async () => { resolve(); await pending; });
    expect(socket.pttStart).not.toHaveBeenCalled();
    expect(socket.pttEnd).not.toHaveBeenCalled();
    expect(audio.stopCapture).toHaveBeenCalled();
  });

  it("releases an already keyed radio when the shared microphone fails opening another net", async () => {
    const { result, audio, socket } = setup();
    await act(async () => result.current.start("one"));
    audio.startCapture.mockRejectedValueOnce(new Error("Microphone disconnected"));
    await act(async () => result.current.start("two"));
    expect(socket.pttEnd).toHaveBeenCalledWith("one");
    expect(socket.pttStart).toHaveBeenCalledTimes(1);
    expect(audio.stopCapture).toHaveBeenCalled();
  });

  it("does not confuse a cancelled microphone request with a later hold on the same radio", async () => {
    const { result, audio, socket } = setup();
    let resolve!: () => void;
    audio.startCapture.mockImplementationOnce(() => new Promise<void>((done) => { resolve = done; }));
    let pending!: Promise<void>;
    act(() => { pending = result.current.start("one"); });
    act(() => result.current.end("one"));
    await act(async () => result.current.start("one"));
    await act(async () => { resolve(); await pending; });
    expect(socket.pttStart).toHaveBeenCalledTimes(1);
    act(() => result.current.end("one"));
    expect(socket.pttEnd).toHaveBeenCalledTimes(1);
  });

  it("keeps a newer hold releasable when the old start/end acknowledgements arrive late", async () => {
    const { result, socket, emit } = setup();
    await act(async () => result.current.start("one"));
    act(() => result.current.end("one"));
    await act(async () => result.current.start("one"));
    emit("ptt_started", { radio_id: "one", sync_applies: false });
    expect(result.current.phases.one).toBeUndefined();
    emit("ptt_ended", { radio_id: "one" });
    emit("ptt_started", { radio_id: "one", sync_applies: false });
    expect(result.current.phases.one).toBe("TX");
    act(() => result.current.end("one"));
    expect(socket.pttEnd).toHaveBeenCalledTimes(2);
    expect(result.current.phases.one).toBeUndefined();
  });

  it("releases every net on focus loss and ignores late TX state", async () => {
    const { result, socket, audio, emit } = setup(true);
    await act(async () => { await result.current.start("one"); await result.current.start("two"); });
    emit("ptt_started", { radio_id: "two", sync_applies: true });
    act(() => fireEvent(window, new Event("blur")));
    expect(socket.instrPttEnd).toHaveBeenCalledWith("one");
    expect(socket.instrPttAbort).toHaveBeenCalledWith("two");
    expect(audio.stopCapture).toHaveBeenCalled();
    emit("secure_tx", { radio_id: "two" });
    expect(result.current.phases).toEqual({});
  });

  it("releases a held shortcut even when key-up occurs over an input", async () => {
    const { result, socket } = setup();
    await act(async () => { fireEvent.keyDown(window, { code: "Numpad1" }); });
    const input = document.createElement("input");
    document.body.append(input);
    fireEvent.keyUp(input, { code: "Numpad1" });
    expect(socket.pttEnd).toHaveBeenCalledWith("one");
    expect(result.current.phases).toEqual({});
    input.remove();
  });

  it("does not key while typing or holding modifiers and saves usable laptop shortcuts", async () => {
    const { result, socket } = setup();
    const input = document.createElement("input");
    document.body.append(input);
    await act(async () => {
      fireEvent.keyDown(input, { code: "Numpad1" });
      fireEvent.keyDown(window, { code: "Numpad1", ctrlKey: true });
    });
    expect(socket.pttStart).not.toHaveBeenCalled();
    act(() => result.current.setShortcut(0, "KeyP"));
    await act(async () => { fireEvent.keyDown(window, { code: "KeyP" }); });
    expect(socket.pttStart).toHaveBeenCalledWith("7.0000 MHz", "Plain", "one");
    expect(JSON.parse(localStorage.getItem("pivot.ptt.shortcuts.trainee")!)[0]).toBe("KeyP");
    input.remove();
  });

  it("stops capture on close, microphone readiness loss and unmount", async () => {
    const { result, socket, audio, emit, rerender, props, unmount } = setup();
    await act(async () => result.current.start("one"));
    emit("close");
    expect(audio.stopCapture).toHaveBeenCalled();
    await act(async () => result.current.start("one"));
    rerender({ ...props, enabled: false });
    expect(socket.pttEnd).toHaveBeenCalledTimes(2);
    rerender(props);
    await act(async () => result.current.start("one"));
    unmount();
    expect(socket.pttEnd).toHaveBeenCalledTimes(3);
  });

  it("does not start when disconnected and clears active requests on server errors", async () => {
    const { result, socket, audio, emit } = setup();
    socket.connected = false;
    await act(async () => result.current.start("one"));
    expect(audio.startCapture).not.toHaveBeenCalled();
    socket.connected = true;
    await act(async () => result.current.start("one"));
    emit("error", { detail: "Radio busy" });
    expect(result.current.error).toBe("Radio busy");
    expect(socket.pttEnd).toHaveBeenCalled();
    emit("ptt_started", { radio_id: "one", sync_applies: false });
    expect(result.current.phases).toEqual({});
  });
  it("discards a rejected start after release so the next hold receives its acknowledgement", async () => {
    const { result, emit } = setup();
    await act(async () => result.current.start("one"));
    act(() => result.current.end("one"));
    emit("error", { detail: "Radio busy" });
    emit("ptt_ended", { radio_id: "one" });
    await act(async () => result.current.start("one"));
    emit("ptt_started", { radio_id: "one", sync_applies: false });
    expect(result.current.phases.one).toBe("TX");
    act(() => result.current.end("one"));
    expect(result.current.phases).toEqual({});
  });

});
