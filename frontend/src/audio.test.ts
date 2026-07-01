import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { loadVolume, parseTaggedAudio, saveVolume, pcmLevel } from "./audio";

// Build an instructor-style tagged frame: [1-byte id length][radio_id][PCM…].
function taggedFrame(radioId: string, samples: number[]): ArrayBuffer {
  const id = new TextEncoder().encode(radioId);
  const pcm = new Int16Array(samples);
  const out = new Uint8Array(1 + id.length + pcm.byteLength);
  out[0] = id.length;
  out.set(id, 1);
  out.set(new Uint8Array(pcm.buffer), 1 + id.length);
  return out.buffer;
}

describe("parseTaggedAudio", () => {
  it("recovers the radio_id and PCM payload", () => {
    const { radioId, pcm } = parseTaggedAudio(taggedFrame("instr-1", [1, -2, 3]));
    expect(radioId).toBe("instr-1");
    expect(Array.from(new Int16Array(pcm))).toEqual([1, -2, 3]);
  });

  it("handles an odd header length without misaligning the PCM", () => {
    // "instr-10" is 8 bytes → payload starts at an odd offset; slicing must copy.
    const { radioId, pcm } = parseTaggedAudio(taggedFrame("instr-10", [7, 8]));
    expect(radioId).toBe("instr-10");
    expect(Array.from(new Int16Array(pcm))).toEqual([7, 8]);
  });
});

describe("volume persistence", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to full volume when unset", () => {
    expect(loadVolume("trainee")).toBe(1);
  });

  it("round-trips and clamps to 0–1", () => {
    saveVolume("instr.instr-2", 0.4);
    expect(loadVolume("instr.instr-2")).toBe(0.4);
    saveVolume("trainee", 5);
    expect(loadVolume("trainee")).toBe(1);
    saveVolume("trainee", -1);
    expect(loadVolume("trainee")).toBe(0);
  });
});

describe("pcmLevel", () => {
  it("returns 0 for empty buffers", () => {
    const pcm = new Int16Array([]).buffer;
    expect(pcmLevel(pcm)).toBe(0);
  });

  it("returns 0 for pure silence", () => {
    const pcm = new Int16Array([0, 0, 0, 0]).buffer;
    expect(pcmLevel(pcm)).toBe(0);
  });

  it("caps at 1 for full scale audio", () => {
    const pcm = new Int16Array([32767, -32768, 32767]).buffer;
    expect(pcmLevel(pcm)).toBe(1);
  });

  it("computes expected level for mid-scale audio", () => {
    const pcm = new Int16Array([16384, -16384, 16384]).buffer;
    expect(pcmLevel(pcm)).toBeCloseTo(0.88388, 4);
  });
});

describe("playSyncTone", () => {
  let mockGain: any;
  let mockOscillators: any[];
  let mockAudioContext: any;

  beforeEach(() => {
    mockGain = {
      gain: {
        setValueAtTime: vi.fn(),
        exponentialRampToValueAtTime: vi.fn(),
      },
      connect: vi.fn(),
    };

    mockOscillators = [];

    mockAudioContext = vi.fn().mockImplementation(() => ({
      currentTime: 100,
      createGain: vi.fn().mockReturnValue(mockGain),
      createOscillator: vi.fn().mockImplementation(() => {
        const osc = {
          type: "",
          frequency: { value: 0 },
          connect: vi.fn(),
          start: vi.fn(),
          stop: vi.fn(),
        };
        mockOscillators.push(osc);
        return osc;
      }),
      destination: {},
    }));

    vi.stubGlobal("AudioContext", mockAudioContext);
    vi.stubGlobal("webkitAudioContext", undefined);
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("plays a tone with default duration", async () => {
    const { playSyncTone } = await import("./audio");
    playSyncTone();

    // verify audio context was created
    expect(mockAudioContext).toHaveBeenCalledTimes(1);

    // verify gain was created and configured
    expect(mockGain.gain.setValueAtTime).toHaveBeenCalledWith(0.0001, 100);
    expect(mockGain.gain.exponentialRampToValueAtTime).toHaveBeenCalledWith(0.25, 100 + 0.02);
    expect(mockGain.gain.setValueAtTime).toHaveBeenCalledWith(0.25, 100 + 0.3 - 0.04);
    expect(mockGain.gain.exponentialRampToValueAtTime).toHaveBeenCalledWith(0.0001, 100 + 0.3);
    expect(mockGain.connect).toHaveBeenCalled();

    // verify 2 oscillators were created
    expect(mockOscillators).toHaveLength(2);

    // verify oscillator 1 (1200 Hz)
    expect(mockOscillators[0].type).toBe("square");
    expect(mockOscillators[0].frequency.value).toBe(1200);
    expect(mockOscillators[0].connect).toHaveBeenCalledWith(mockGain);
    expect(mockOscillators[0].start).toHaveBeenCalledWith(100);
    expect(mockOscillators[0].stop).toHaveBeenCalledWith(100 + 0.3);

    // verify oscillator 2 (1600 Hz)
    expect(mockOscillators[1].type).toBe("square");
    expect(mockOscillators[1].frequency.value).toBe(1600);
    expect(mockOscillators[1].connect).toHaveBeenCalledWith(mockGain);
    expect(mockOscillators[1].start).toHaveBeenCalledWith(100);
    expect(mockOscillators[1].stop).toHaveBeenCalledWith(100 + 0.3);
  });

  it("plays a tone with custom duration", async () => {
    const { playSyncTone } = await import("./audio");
    playSyncTone(500);

    expect(mockGain.gain.setValueAtTime).toHaveBeenCalledWith(0.25, 100 + 0.5 - 0.04);
    expect(mockGain.gain.exponentialRampToValueAtTime).toHaveBeenCalledWith(0.0001, 100 + 0.5);

    expect(mockOscillators[0].stop).toHaveBeenCalledWith(100 + 0.5);
    expect(mockOscillators[1].stop).toHaveBeenCalledWith(100 + 0.5);
  });
});
