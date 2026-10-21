import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AudioIO } from "./audio";

class MicTrack extends EventTarget {
  enabled = true;
  muted = false;
  readyState = "live";
  stop = vi.fn(() => { this.readyState = "ended"; });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function streamFor(track: MicTrack) {
  return { getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream;
}

describe("microphone readiness and capture", () => {
  let track: MicTrack;
  let getUserMedia: ReturnType<typeof vi.fn>;
  let nodes: { name: string; port: { onmessage: ((event: { data: Float32Array }) => void) | null }; disconnect: ReturnType<typeof vi.fn> }[];
  let io: AudioIO;

  beforeEach(() => {
    localStorage.clear();
    nodes = [];
    track = new MicTrack();
    getUserMedia = vi.fn().mockResolvedValue(streamFor(track));
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
    vi.stubGlobal("AudioContext", vi.fn().mockImplementation(() => ({
      audioWorklet: { addModule: vi.fn().mockResolvedValue(undefined) },
      destination: {}, state: "running", close: vi.fn().mockResolvedValue(undefined),
      createMediaStreamSource: vi.fn(() => ({ connect: vi.fn(), disconnect: vi.fn() })),
      createGain: vi.fn(() => ({ gain: { value: 0 }, connect: vi.fn(), disconnect: vi.fn() })),
    })));
    vi.stubGlobal("AudioWorkletNode", vi.fn().mockImplementation((_context, name: string) => {
      const node = { name, connect: vi.fn(), disconnect: vi.fn(), port: { onmessage: null, postMessage: vi.fn() } };
      nodes.push(node);
      return node;
    }));
    io = new AudioIO();
  });

  afterEach(() => {
    io.close();
    vi.unstubAllGlobals();
  });

  const frame = () => ({ data: new Float32Array([0.5, -0.5]) });
  const micNode = (nodes: { name: string; port: { onmessage: ((event: { data: Float32Array }) => void) | null } }[]) => nodes.find((node) => node.name === "pivot-mic")!;

  it("surfaces denied permission and recovers with an explicit retry", async () => {
    getUserMedia.mockRejectedValueOnce(new DOMException("Permission denied", "NotAllowedError"));
    const listener = vi.fn();
    io.onMicState(listener);
    await expect(io.startCapture(vi.fn())).rejects.toThrow("Permission denied");
    expect(io.getMicState()).toMatchObject({ status: "error", error: expect.stringContaining("Allow microphone access") });
    await io.prewarm();
    expect(io.getMicState().status).toBe("ready");
    expect(listener).toHaveBeenCalledWith(expect.objectContaining({ status: "error" }));
  });

  it("measures input while warm without transmitting it until capture starts", async () => {
    const send = vi.fn();
    await io.prewarm();
    micNode(nodes).port.onmessage!(frame());
    expect(io.getInputLevel()).toBe(1);
    expect(send).not.toHaveBeenCalled();
    await io.startCapture(send);
    micNode(nodes).port.onmessage!(frame());
    expect(send).toHaveBeenCalledTimes(1);
    io.stopCapture();
    micNode(nodes).port.onmessage!(frame());
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("does not start sending when released while microphone permission is pending", async () => {
    const pending = deferred<MediaStream>();
    getUserMedia.mockReturnValueOnce(pending.promise);
    const send = vi.fn();
    const started = io.startCapture(send);
    // Wait until the request is pending, then release the pressel.
    await vi.waitFor(() => expect(getUserMedia).toHaveBeenCalled());
    io.stopCapture();
    pending.resolve(streamFor(track));
    await expect(started).rejects.toMatchObject({ name: "AbortError" });
    micNode(nodes).port.onmessage!(frame());
    expect(send).not.toHaveBeenCalled();
  });

  it("shares one open request across simultaneous radios", async () => {
    await Promise.all([io.startCapture(vi.fn()), io.startCapture(vi.fn()), io.prewarm()]);
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(nodes.filter((node) => node.name === "pivot-mic")).toHaveLength(1);
  });

  it("stops sending and reports a microphone disconnect", async () => {
    const send = vi.fn();
    await io.startCapture(send);
    const node = micNode(nodes);
    track.readyState = "ended";
    track.dispatchEvent(new Event("ended"));
    node.port.onmessage!(frame());
    expect(send).not.toHaveBeenCalled();
    expect(io.getMicState()).toMatchObject({ status: "error", error: expect.stringContaining("disconnected") });
    expect(io.getInputLevel()).toBe(0);
    expect(track.stop).toHaveBeenCalled();
  });

  it("reports hardware mute and requires a fresh press after unmute", async () => {
    const send = vi.fn();
    await io.startCapture(send);
    track.muted = true;
    track.dispatchEvent(new Event("mute"));
    expect(io.getMicState().status).toBe("error");
    track.muted = false;
    track.dispatchEvent(new Event("unmute"));
    expect(io.getMicState().status).toBe("ready");
    micNode(nodes).port.onmessage!(frame());
    expect(send).not.toHaveBeenCalled();
  });

  it("switches devices, stops the old stream and persists the selection", async () => {
    await io.prewarm();
    const replacement = new MicTrack();
    getUserMedia.mockResolvedValueOnce(streamFor(replacement));
    await io.setInputDevice("headset");
    expect(track.stop).toHaveBeenCalledTimes(1);
    expect(getUserMedia).toHaveBeenLastCalledWith({ audio: expect.objectContaining({ deviceId: { exact: "headset" } }) });
    expect(io.getMicState()).toMatchObject({ status: "ready", deviceId: "headset" });
    expect(new AudioIO().getMicState().deviceId).toBe("headset");
  });

  it("releases a stream that resolves after the audio session closes", async () => {
    const pending = deferred<MediaStream>();
    getUserMedia.mockReturnValueOnce(pending.promise);
    const opening = io.prewarm();
    await vi.waitFor(() => expect(getUserMedia).toHaveBeenCalled());
    io.close();
    pending.resolve(streamFor(track));
    await expect(opening).rejects.toMatchObject({ name: "AbortError" });
    expect(track.stop).toHaveBeenCalled();
    expect(io.getMicState().status).toBe("idle");
  });
});
