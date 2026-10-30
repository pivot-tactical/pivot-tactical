// Client-side audio: local feedback tones + the streaming mic/playback engine.
// The crypto sync tone plays only to the transmitting operator and is NEVER
// transmitted (spec §3.2.3, §4.3). Voice is carried as 16 kHz mono PCM over the
// WebSocket (spec §6.3).

const SR = 16000;

let toneCtx: AudioContext | null = null;
function localCtx(): AudioContext | null {
  if (!toneCtx) {
    const Ctx = window.AudioContext || (window as any).webkitAudioContext;
    if (Ctx) toneCtx = new Ctx();
  }
  return toneCtx;
}

// A short two-tone (KY-57 style) burst, ~0.3 s, local only.
export function playSyncTone(durationMs = 300) {
  const ac = localCtx();
  if (!ac) return;
  const now = ac.currentTime;
  const gain = ac.createGain();
  gain.gain.setValueAtTime(0.0001, now);
  gain.gain.exponentialRampToValueAtTime(0.25, now + 0.02);
  gain.gain.setValueAtTime(0.25, now + durationMs / 1000 - 0.04);
  gain.gain.exponentialRampToValueAtTime(0.0001, now + durationMs / 1000);
  gain.connect(ac.destination);
  for (const f of [1200, 1600]) {
    const osc = ac.createOscillator();
    osc.type = "square";
    osc.frequency.value = f;
    osc.connect(gain);
    osc.start(now);
    osc.stop(now + durationMs / 1000);
  }
}

// PTT click feedback on key-down/up.
export function playClick(freq = 900) {
  const ac = localCtx();
  if (!ac) return;
  const now = ac.currentTime;
  const osc = ac.createOscillator();
  const gain = ac.createGain();
  osc.frequency.value = freq;
  gain.gain.setValueAtTime(0.2, now);
  gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.05);
  osc.connect(gain);
  gain.connect(ac.destination);
  osc.start(now);
  osc.stop(now + 0.05);
}

// Asymmetric scaling matches the Int16 range: negatives use 0x8000 so -1 maps to
// the true floor (-32768), positives use 0x7fff so +1 maps to the ceiling (32767).
// Only one branch's clamp is needed per sample, which is why the sign test comes
// first. Out-of-range samples saturate rather than wrap; NaN fails the sign test,
// survives Math.min as NaN, and lands on 0 via Int16Array's coercion.
export function floatToPcm16(f32: Float32Array): ArrayBuffer {
  const len = f32.length;
  const i16 = new Int16Array(len);
  for (let i = 0; i < len; i++) {
    const s = f32[i];
    // Inline Math.max/min checks for performance in the hot loop
    i16[i] = s < 0 ? (s < -1 ? -1 : s) * 0x8000 : (s > 1 ? 1 : s) * 0x7fff;
  }
  return i16.buffer;
}

function pcm16ToFloat(buf: ArrayBuffer, gain = 1): Float32Array {
  const i16 = new Int16Array(buf);
  const len = i16.length;
  const f32 = new Float32Array(len);
  const factor = 0.000030517578125 * gain;
  for (let i = 0; i < len; i++) f32[i] = i16[i] * factor;
  return f32;
}

// Instructor audio frames are tagged with their source radio so one mixed
// playback stream can carry several radios at independent headset volumes:
// [1-byte id length][radio_id ascii][PCM16LE…]. Trainee frames are untagged
// (one radio per socket). Mirrors `_tagged_sink` in server/pivot/api/ws.py.
export function parseTaggedAudio(buf: ArrayBuffer): { radioId: string; pcm: ArrayBuffer } {
  const bytes = new Uint8Array(buf);
  const len = bytes[0];
  // NOTE (intentional): a fresh ``TextDecoder`` per frame. Do not hoist it to a
  // module-level singleton as a "hot loop" optimisation: constructing one is
  // cheap next to the buffer copy below, and frames arrive at ~50/s, so there is
  // nothing measurable to save.
  const radioId = new TextDecoder().decode(bytes.subarray(1, 1 + len));
  // Slice (copy) so the PCM starts at offset 0 — an odd header length would
  // otherwise break Int16Array's alignment requirement on a shared buffer.
  return { radioId, pcm: buf.slice(1 + len) };
}

// Perceptual receive level (0–1) of one PCM frame, for the live signal meters.
// Computed on the raw frame (before the headset volume gain) so the meter shows
// what is on the channel, not how loud the operator's earpiece is. The sqrt
// softening lets the faint clean-channel hiss register near the bottom while a
// keyed voice drives the bar toward full scale.
//
// NOTE (intentional): this is a plain single-accumulator loop. Do not manually
// unroll it (4-way or otherwise) or add further micro-optimisations. A frame is
// only ~320 samples, so the meter costs microseconds per frame, and V8 already
// optimises a simple counted loop; hand-unrolling adds code and a tail loop for
// no measurable gain. The loop was already tightened once (hoisted length and
// reciprocal) — leave it as is.
export function pcmLevel(pcm: ArrayBuffer): number {
  const i16 = new Int16Array(pcm);
  const len = i16.length;
  if (len === 0) return 0;
  let sum = 0;
  for (let i = 0; i < len; i++) {
    const s = i16[i];
    sum += s * s;
  }
  const rms = Math.sqrt(sum / len) / 0x8000;
  const level = Math.sqrt(rms) * 1.25;
  return level > 1 ? 1 : level;
}

// Persisted per-radio headset volume (0–1). Keyed so a trainee keeps one
// setting and the instructor keeps one per radio across refreshes.
export function loadVolume(key: string): number {
  const raw = localStorage.getItem(`pivot.vol.${key}`);
  if (raw == null) return 1;
  const v = parseFloat(raw);
  return Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 1;
}

export function saveVolume(key: string, volume: number): void {
  localStorage.setItem(`pivot.vol.${key}`, String(Math.max(0, Math.min(1, volume))));
}

export interface MicState {
  status: "idle" | "opening" | "ready" | "error";
  error: string | null;
  deviceId: string;
}

function microphoneError(error: unknown): string {
  const name = error && typeof error === "object" && "name" in error ? String(error.name) : "";
  if (name === "NotAllowedError" || name === "SecurityError")
    return "Microphone permission is blocked. Allow microphone access in your browser, then retry.";
  if (name === "NotFoundError" || name === "OverconstrainedError")
    return "The selected microphone is unavailable. Connect it or choose another microphone, then retry.";
  if (name === "NotReadableError")
    return "The microphone could not be opened. Check that another application is not using it, then retry.";
  if (!navigator.mediaDevices?.getUserMedia)
    return "Microphone access requires HTTPS or localhost. Open the instructor's HTTPS address, then retry.";
  return "Microphone or audio setup failed. Check your device and browser permissions, then retry.";
}

/** Streaming voice I/O. The mic stays warm; only keyed frames leave the browser. */
export class AudioIO {
  private ctx: AudioContext | null = null;
  private player: AudioWorkletNode | null = null;
  private initPromise: Promise<void> | null = null;
  private mic: {
    stream: MediaStream;
    src: MediaStreamAudioSourceNode;
    node: AudioWorkletNode;
    mute: GainNode;
    cleanup: () => void;
  } | null = null;
  private micPromise: Promise<void> | null = null;
  private micEpoch = 0;
  private captureEpoch = 0;
  private sending = false;
  private onFrame: ((pcm: ArrayBuffer) => void) | null = null;
  private micState: MicState = { status: "idle", error: null, deviceId: "" };
  private micListeners = new Set<(state: MicState) => void>();
  private inputLevel = 0;
  private inputLevelAt = 0;
  private volumes = new Map<string, number>();
  private defaultVolume = 1;

  constructor() {
    try { this.micState.deviceId = localStorage.getItem("pivot.mic.device") ?? ""; } catch { /* storage may be disabled */ }
  }

  getMicState = (): MicState => this.micState;

  onMicState = (listener: (state: MicState) => void): (() => void) => {
    this.micListeners.add(listener);
    return () => this.micListeners.delete(listener);
  };

  private setMicState(status: MicState["status"], error: string | null = null) {
    this.micState = { ...this.micState, status, error };
    this.micListeners.forEach((listener) => listener(this.micState));
  }

  getInputLevel(): number {
    return this.micState.status === "ready" && Date.now() - this.inputLevelAt < 250 ? this.inputLevel : 0;
  }

  async setInputDevice(deviceId: string): Promise<void> {
    this.releaseMic();
    this.micState = { ...this.micState, deviceId };
    try { localStorage.setItem("pivot.mic.device", deviceId); } catch { /* storage may be disabled */ }
    await this.prewarm();
  }

  setVolume(volume: number, radioId?: string): void {
    const v = Math.max(0, Math.min(1, volume));
    if (radioId) this.volumes.set(radioId, v);
    else this.defaultVolume = v;
  }

  async init(): Promise<void> {
    if (this.player) {
      if (this.ctx?.state === "suspended") await this.ctx.resume();
      return;
    }
    if (this.initPromise) return this.initPromise;
    if (!this.ctx) this.ctx = new AudioContext({ sampleRate: SR });
    const ctx = this.ctx;
    const p = (async () => {
      await ctx.audioWorklet.addModule("/pivot-mic-worklet.js");
      await ctx.audioWorklet.addModule("/pivot-player-worklet.js");
      if (this.ctx !== ctx) throw new DOMException("Audio setup was cancelled", "AbortError");
      this.player = new AudioWorkletNode(ctx, "pivot-player");
      this.player.connect(ctx.destination);
      if (ctx.state === "suspended") await ctx.resume();
    })();
    this.initPromise = p;
    try { await p; } finally { if (this.initPromise === p) this.initPromise = null; }
  }

  private async openMic(): Promise<void> {
    if (this.mic) {
      const track = this.mic.stream.getAudioTracks()[0];
      if (!track || track.readyState === "ended") this.releaseMic();
      else if (track?.muted || !track?.enabled) {
        this.stopCapture();
        this.setMicState("error", "Microphone is muted or unavailable. Check your device, then retry.");
        throw new Error(this.micState.error!);
      } else {
        try {
          await this.init();
          if (this.micState.status !== "ready") this.setMicState("ready");
        } catch (error) {
          this.stopCapture();
          this.setMicState("error", microphoneError(error));
          throw error;
        }
        return;
      }
    }
    if (this.micPromise) return this.micPromise;
    const epoch = this.micEpoch;
    this.setMicState("opening");
    const p: Promise<void> = (async () => {
      if (!navigator.mediaDevices?.getUserMedia)
        throw new Error("Microphone access requires a secure context (HTTPS or localhost)");
      await this.init();
      if (this.micEpoch !== epoch) throw new DOMException("Microphone setup was cancelled", "AbortError");
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1, echoCancellation: true, noiseSuppression: false, autoGainControl: true,
          ...(this.micState.deviceId ? { deviceId: { exact: this.micState.deviceId } } : {}),
        },
      });
      if (this.micEpoch !== epoch) {
        stream.getTracks().forEach((track) => track.stop());
        throw new DOMException("Microphone setup was cancelled", "AbortError");
      }
      let src: MediaStreamAudioSourceNode | null = null;
      let node: AudioWorkletNode | null = null;
      let mute: GainNode | null = null;
      try {
        const track = stream.getAudioTracks()[0];
        if (!track || track.readyState === "ended") throw new DOMException("No active microphone", "NotFoundError");
        src = this.ctx!.createMediaStreamSource(stream);
        node = new AudioWorkletNode(this.ctx!, "pivot-mic");
        mute = this.ctx!.createGain();
        mute.gain.value = 0;
        src.connect(node);
        node.connect(mute);
        mute.connect(this.ctx!.destination);
        node.port.onmessage = (event) => {
          if (this.mic?.stream !== stream) return;
          const frame = event.data as Float32Array;
          let sum = 0;
          for (const sample of frame) sum += sample * sample;
          this.inputLevel = frame.length ? Math.min(1, Math.sqrt(sum / frame.length) * 3) : 0;
          this.inputLevelAt = Date.now();
          if (this.sending && this.onFrame && this.micState.status === "ready")
            this.onFrame(floatToPcm16(frame));
        };
        const ended = () => {
          if (this.mic?.stream !== stream) return;
          this.releaseMic();
          this.setMicState("error", "Microphone disconnected. Connect it or choose another microphone, then retry.");
        };
        const muted = () => {
          if (this.mic?.stream !== stream) return;
          this.stopCapture();
          this.setMicState("error", "Microphone is muted or unavailable. Check your device, then retry.");
        };
        const unmuted = () => {
          if (this.mic?.stream === stream) this.setMicState("ready");
        };
        track.addEventListener("ended", ended);
        track.addEventListener("mute", muted);
        track.addEventListener("unmute", unmuted);
        this.mic = { stream, src, node, mute, cleanup: () => {
          track.removeEventListener("ended", ended);
          track.removeEventListener("mute", muted);
          track.removeEventListener("unmute", unmuted);
        } };
        if (track.muted || !track.enabled) muted();
        else this.setMicState("ready");
        if (this.micState.status !== "ready") throw new Error(this.micState.error!);
      } catch (error) {
        if (this.mic?.stream === stream) {
          this.mic.cleanup();
          this.mic = null;
        }
        stream.getTracks().forEach((track) => track.stop());
        src?.disconnect();
        node?.disconnect();
        mute?.disconnect();
        throw error;
      }
    })();
    this.micPromise = p;
    try {
      await p;
    } catch (error) {
      if (this.micEpoch === epoch) {
        this.stopCapture();
        this.setMicState("error", microphoneError(error));
      }
      throw error;
    } finally {
      if (this.micPromise === p) this.micPromise = null;
    }
  }

  async prewarm(): Promise<void> { await this.openMic(); }

  async startCapture(onFrame: (pcm: ArrayBuffer) => void): Promise<void> {
    const epoch = this.captureEpoch;
    await this.openMic();
    if (this.captureEpoch !== epoch || this.micState.status !== "ready")
      throw new DOMException("Transmission was cancelled", "AbortError");
    this.onFrame = onFrame;
    this.sending = true;
  }

  stopCapture(): void {
    this.captureEpoch++;
    this.sending = false;
    this.onFrame = null;
  }

  play(pcm: ArrayBuffer, radioId?: string): void {
    if (!this.player) return;
    if (this.ctx?.state === "suspended") void this.ctx.resume().catch(() => {});
    const gain = radioId ? this.volumes.get(radioId) ?? this.defaultVolume : this.defaultVolume;
    const f32 = pcm16ToFloat(pcm, gain);
    this.player.port.postMessage(f32, [f32.buffer]);
  }

  private releaseMic(): void {
    this.stopCapture();
    this.micEpoch++;
    this.micPromise = null;
    this.inputLevel = 0;
    if (this.mic) {
      const mic = this.mic;
      this.mic = null;
      mic.cleanup();
      mic.stream.getTracks().forEach((track) => track.stop());
      mic.src.disconnect();
      mic.node.disconnect();
      mic.mute.disconnect();
    }
  }

  close(): void {
    this.releaseMic();
    void this.ctx?.close().catch(() => {});
    this.ctx = null;
    this.player = null;
    this.initPromise = null;
    this.setMicState("idle");
  }
}
