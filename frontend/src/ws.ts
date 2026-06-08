// WebSocket client: state sync + PTT lifecycle + (scaffolded) WebRTC signalling.
// Spec §6.2, §6.3. Connects as a trainee ({name, trainee_id}) or, with a bearer
// token, as the instructor (receives the live event log + drives instr_* radios).

type Handler = (payload: any) => void;

export class PivotSocket {
  private ws: WebSocket | null = null;
  private handlers = new Map<string, Set<Handler>>();
  private query: () => Record<string, string>;
  private heartbeat?: number;
  private reconnectTimer?: number;
  private closed = false;
  private audioHandler?: (buf: ArrayBuffer) => void;

  // The query may be a function so the URL is rebuilt on every (re)connect. The
  // instructor passes a token getter: a long scenario slides its token, and a
  // reconnect must use the *current* one or it would fall back to a trainee.
  constructor(query: Record<string, string> | (() => Record<string, string>)) {
    this.query = typeof query === "function" ? query : () => query;
  }

  private buildUrl(): string {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const params = new URLSearchParams(this.query());
    return `${proto}://${location.host}/ws?${params.toString()}`;
  }

  connect() {
    this.closed = false;
    this.ws = new WebSocket(this.buildUrl());
    this.ws.binaryType = "arraybuffer";
    this.ws.onopen = () => {
      this.emit("open", {});
      this.heartbeat = window.setInterval(() => this.send("heartbeat", {}), 10000);
    };
    this.ws.onmessage = (ev) => {
      if (typeof ev.data !== "string") {
        this.audioHandler?.(ev.data as ArrayBuffer); // binary = rendered PCM
        return;
      }
      try {
        const msg = JSON.parse(ev.data);
        this.emit(msg.type, msg.payload);
      } catch {
        /* ignore malformed frames */
      }
    };
    this.ws.onclose = () => {
      this.emit("close", {});
      window.clearInterval(this.heartbeat);
      if (!this.closed) {
        this.reconnectTimer = window.setTimeout(() => this.connect(), 1500);
      }
    };
  }

  disconnect() {
    this.closed = true;
    window.clearInterval(this.heartbeat);
    window.clearTimeout(this.reconnectTimer);
    this.ws?.close();
    this.ws = null;
  }

  on(type: string, handler: Handler) {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set());
    this.handlers.get(type)!.add(handler);
    return () => this.handlers.get(type)?.delete(handler);
  }

  private emit(type: string, payload: any) {
    this.handlers.get(type)?.forEach((h) => h(payload));
  }

  send(type: string, payload: any) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type, payload }));
    }
  }

  // Voice frames (16 kHz mono PCM) — sent while keyed, received from listeners.
  sendAudio(pcm: ArrayBuffer) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(pcm);
  }
  onAudio(handler: (buf: ArrayBuffer) => void) {
    this.audioHandler = handler;
  }

  // Trainee PTT/control (operates the socket's own radio).
  pttStart(frequency: string, txMode: string) {
    this.send("ptt_start", { frequency, tx_mode: txMode });
  }
  pttEnd() {
    this.send("ptt_end", {});
  }
  pttAbort() {
    this.send("ptt_abort", {});
  }
  tune(frequency: string) {
    this.send("tune", { frequency });
  }
  modeChange(mode: string) {
    this.send("mode_change", { mode });
  }

  // Instructor control (targets a specific instructor radio by id).
  instrPttStart(radioId: string, frequency: string, txMode: string) {
    this.send("instr_ptt_start", { radio_id: radioId, frequency, tx_mode: txMode });
  }
  instrPttEnd(radioId: string) {
    this.send("instr_ptt_end", { radio_id: radioId });
  }
  instrPttAbort(radioId: string) {
    this.send("instr_ptt_abort", { radio_id: radioId });
  }
  instrTune(radioId: string, frequency: string) {
    this.send("instr_tune", { radio_id: radioId, frequency });
  }
  instrMode(radioId: string, mode: string) {
    this.send("instr_mode", { radio_id: radioId, mode });
  }
}
