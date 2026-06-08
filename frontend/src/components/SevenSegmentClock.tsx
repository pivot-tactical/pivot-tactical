import { useEffect, useState } from "react";

// Live seven-segment-style ops-room clock in the configured display timezone
// (spec §3.8, §7.3). All times are UTC server-side; only presentation differs.
export function SevenSegmentClock({ timezone }: { timezone: string }) {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 250);
    return () => window.clearInterval(id);
  }, []);

  let text = "--:--:--";
  try {
    text = new Intl.DateTimeFormat("en-GB", {
      timeZone: timezone || "UTC",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(now);
  } catch {
    text = now.toISOString().slice(11, 19);
  }

  return (
    <div className="seven-seg" title={`Display timezone: ${timezone}`}>
      <span className="seven-seg__ghost">88:88:88</span>
      <span className="seven-seg__value">{text}</span>
      <div className="seven-seg__zone">{timezone || "UTC"}</div>
    </div>
  );
}
