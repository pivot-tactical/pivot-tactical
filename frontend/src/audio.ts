// Client-side audio helpers: the local crypto sync tone and mic capture.
// The sync tone plays to the transmitting trainee's own headset only and is
// NEVER transmitted on the net (spec §3.2.3, §4.3).

let ctx: AudioContext | null = null;
function audioCtx(): AudioContext {
  if (!ctx) ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
  return ctx;
}

// A short two-tone (KY-57 style) burst, ~0.3s, local only.
export function playSyncTone(durationMs = 300) {
  const ac = audioCtx();
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

// PTT click feedback on key-down/up (receive-side squelch tones are server-side).
export function playClick(freq = 900) {
  const ac = audioCtx();
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

// Mic capture for transmit. The captured stream is handed to the WebRTC peer
// connection by the (scaffolded) transport; here we manage permission + level.
export class MicCapture {
  private stream: MediaStream | null = null;

  async start(): Promise<MediaStream> {
    if (!this.stream) {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: false, autoGainControl: true },
      });
    }
    return this.stream;
  }

  stop() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
  }

  get active() {
    return this.stream !== null;
  }
}
