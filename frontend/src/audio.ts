// Client-side audio: local feedback tones + the streaming mic/playback engine.
// The crypto sync tone plays only to the transmitting operator and is NEVER
// transmitted (spec §3.2.3, §4.3). Voice is carried as 16 kHz mono PCM over the
// WebSocket (spec §6.3).

const SR = 16000;

let toneCtx: AudioContext | null = null;
function localCtx(): AudioContext {
  if (!toneCtx) toneCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
  return toneCtx;
}

// A short two-tone (KY-57 style) burst, ~0.3 s, local only.
export function playSyncTone(durationMs = 300) {
  const ac = localCtx();
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

function floatToPcm16(f32: Float32Array): ArrayBuffer {
  const i16 = new Int16Array(f32.length);
  for (let i = 0; i < f32.length; i++) {
    const s = Math.max(-1, Math.min(1, f32[i]));
    i16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return i16.buffer;
}

function pcm16ToFloat(buf: ArrayBuffer): Float32Array {
  const i16 = new Int16Array(buf);
  const f32 = new Float32Array(i16.length);
  for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 0x8000;
  return f32;
}

// Instructor audio frames are tagged with their source radio so one mixed
// playback stream can carry several radios at independent headset volumes:
// [1-byte id length][radio_id ascii][PCM16LE…]. Trainee frames are untagged
// (one radio per socket). Mirrors `_tagged_sink` in server/pivot/api/ws.py.
export function parseTaggedAudio(buf: ArrayBuffer): { radioId: string; pcm: ArrayBuffer } {
  const bytes = new Uint8Array(buf);
  const len = bytes[0];
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
export function pcmLevel(pcm: ArrayBuffer): number {
  const i16 = new Int16Array(pcm);
  if (i16.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < i16.length; i++) {
    const s = i16[i] / 0x8000;
    sum += s * s;
  }
  const rms = Math.sqrt(sum / i16.length);
  return Math.min(1, Math.sqrt(rms) * 1.25);
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

/**
 * Streaming voice I/O over the WebSocket. One 16 kHz AudioContext handles both
 * mic capture (while keyed) and playback (always). Must be initialised from a
 * user gesture (join / first PTT) to satisfy browser autoplay rules.
 */
export class AudioIO {
  private ctx: AudioContext | null = null;
  private player: AudioWorkletNode | null = null;
  private mic: {
    stream: MediaStream;
    src: MediaStreamAudioSourceNode;
    node: AudioWorkletNode;
    mute: GainNode;
  } | null = null;
  // Per-radio output gain for the headset volume sliders. The instructor mixes
  // several radios into this one player, so gain is applied per frame keyed by
  // the source radio_id; a trainee has a single radio and uses the default.
  private volumes = new Map<string, number>();
  private defaultVolume = 1;

  // Set the playback volume (0–1). Pass a radio_id to scope it to one of the
  // instructor's radios; omit it for the single trainee radio.
  setVolume(volume: number, radioId?: string): void {
    const v = Math.max(0, Math.min(1, volume));
    if (radioId) this.volumes.set(radioId, v);
    else this.defaultVolume = v;
  }

  async init(): Promise<void> {
    // Guard on player (not ctx): if a previous call created the ctx but then
    // threw before creating the player (e.g. addModule 404), this.ctx is truthy
    // but this.player is null. Checking only ctx would return early and leave
    // audio permanently broken with no way to recover across user gestures.
    if (this.player) {
      if (this.ctx?.state === "suspended") await this.ctx.resume();
      return;
    }
    if (!this.ctx) {
      this.ctx = new AudioContext({ sampleRate: SR });
    }
    await this.ctx.audioWorklet.addModule("/pivot-mic-worklet.js");
    await this.ctx.audioWorklet.addModule("/pivot-player-worklet.js");
    this.player = new AudioWorkletNode(this.ctx, "pivot-player");
    this.player.connect(this.ctx.destination);
  }

  async startCapture(onFrame: (pcm: ArrayBuffer) => void): Promise<void> {
    await this.init();
    if (this.mic) return;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: false, autoGainControl: true },
    });
    const src = this.ctx!.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(this.ctx!, "pivot-mic");
    node.port.onmessage = (e) => onFrame(floatToPcm16(e.data as Float32Array));
    // Route through a muted gain so the graph pulls the worklet without us
    // hearing our own mic.
    const mute = this.ctx!.createGain();
    mute.gain.value = 0;
    src.connect(node);
    node.connect(mute);
    mute.connect(this.ctx!.destination);
    this.mic = { stream, src, node, mute };
  }

  stopCapture(): void {
    if (!this.mic) return;
    this.mic.stream.getTracks().forEach((t) => t.stop());
    this.mic.src.disconnect();
    this.mic.node.disconnect();
    this.mic.mute.disconnect();
    this.mic = null;
  }

  play(pcm: ArrayBuffer, radioId?: string): void {
    if (!this.player) return;
    // Browsers suspend the AudioContext when the tab goes to background. Resume
    // it so hash and voice frames don't vanish when the user alt-tabs back.
    if (this.ctx?.state === "suspended") this.ctx.resume();
    const gain = radioId ? this.volumes.get(radioId) ?? this.defaultVolume : this.defaultVolume;
    const f32 = pcm16ToFloat(pcm);
    if (gain !== 1) for (let i = 0; i < f32.length; i++) f32[i] *= gain;
    this.player.port.postMessage(f32, [f32.buffer]);
  }

  close(): void {
    this.stopCapture();
    this.ctx?.close();
    this.ctx = null;
    this.player = null;
  }
}
