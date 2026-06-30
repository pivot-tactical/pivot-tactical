import { beforeEach, describe, expect, it } from "vitest";
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
