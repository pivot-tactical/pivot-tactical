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

  async init(): Promise<void> {
    if (this.ctx) {
      if (this.ctx.state === "suspended") await this.ctx.resume();
      return;
    }
    this.ctx = new AudioContext({ sampleRate: SR });
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

  play(pcm: ArrayBuffer): void {
    if (!this.player) return;
    const f32 = pcm16ToFloat(pcm);
    this.player.port.postMessage(f32, [f32.buffer]);
  }

  close(): void {
    this.stopCapture();
    this.ctx?.close();
    this.ctx = null;
    this.player = null;
  }
}
