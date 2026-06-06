// A thin fixed banner shown when the live connection to the server drops —
// during a restart (e.g. applying an update) or any network blip. It clears
// itself when the socket reconnects, so the operator always knows whether the
// page is live.

export type ConnState = "online" | "offline" | "restarting";

export function ConnectionBanner({ state }: { state: ConnState }) {
  if (state === "online") return null;
  const restarting = state === "restarting";
  return (
    <div
      role="status"
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        zIndex: 1000,
        textAlign: "center",
        padding: "6px 12px",
        fontSize: "0.9em",
        color: "#000",
        background: restarting ? "#f0c000" : "#e08020",
      }}
    >
      {restarting
        ? "PIVOT is restarting — reconnecting, the page will refresh automatically…"
        : "Connection to the server lost — reconnecting…"}
    </div>
  );
}
