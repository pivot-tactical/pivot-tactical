// Playback worklet: a simple jitter buffer. The main thread posts Float32
// frames (decoded from the server's 16 kHz PCM); this drains them into the
// output, emitting silence when the buffer is empty (spec §6.3).
class PivotPlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.cur = null;
    this.pos = 0;
    this.port.onmessage = (e) => {
      // Cap the buffer so a burst can't grow latency without bound (~2 s).
      if (this.queue.length < 50) this.queue.push(e.data);
    };
  }
  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!out) return true;
    for (let i = 0; i < out.length; i++) {
      if (!this.cur || this.pos >= this.cur.length) {
        this.cur = this.queue.shift() || null;
        this.pos = 0;
      }
      out[i] = this.cur ? this.cur[this.pos++] : 0;
    }
    return true;
  }
}
registerProcessor("pivot-player", PivotPlayerProcessor);
