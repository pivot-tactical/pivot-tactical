// WebSocket client: state sync + PTT lifecycle + (scaffolded) WebRTC signalling.
// Spec §6.2, §6.3.

type Handler = (payload: any) => void;

export class PivotSocket {
  private ws: WebSocket | null = null;
  private handlers = new Map<string, Set<Handler>>();
  private url: string;
  private heartbeat?: number;
  private reconnectTimer?: number;

  constructor(name: string, traineeId: string) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const params = new URLSearchParams({ name, trainee_id: traineeId });
    this.url = `${proto}://${location.host}/ws?${params.toString()}`;
  }

  connect() {
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => {
      this.emit("open", {});
      // Heartbeat keeps the connection and presence alive (§6.2).
      this.heartbeat = window.setInterval(() => this.send("heartbeat", {}), 10000);
    };
    this.ws.onmessage = (ev) => {
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
      // Auto-reconnect; the terminal resumes on its tuned frequency with mode
      // preserved server-side (§8.3).
      this.reconnectTimer = window.setTimeout(() => this.connect(), 1500);
    };
  }

  disconnect() {
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

  // PTT control surface (§3.2.3).
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
}
