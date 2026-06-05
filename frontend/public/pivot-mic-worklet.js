// Mic capture worklet: buffers 16 kHz mono float samples and posts ~40 ms
// frames to the main thread, which converts them to PCM and streams to the
// server over the WebSocket (spec §6.3). The AudioContext is created at 16 kHz,
// so no resampling is needed here.
class PivotMicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.chunks = [];
    this.count = 0;
    this.target = 640; // ~40 ms at 16 kHz
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch && ch.length) {
      this.chunks.push(ch.slice(0));
      this.count += ch.length;
      if (this.count >= this.target) {
        const out = new Float32Array(this.count);
        let o = 0;
        for (const c of this.chunks) {
          out.set(c, o);
          o += c.length;
        }
        this.port.postMessage(out, [out.buffer]);
        this.chunks = [];
        this.count = 0;
      }
    }
    return true;
  }
}
registerProcessor("pivot-mic", PivotMicProcessor);
