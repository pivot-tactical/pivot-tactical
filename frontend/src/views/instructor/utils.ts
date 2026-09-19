import type { LogEntry } from "../../types";

export type Tab = "radios" | "monitor" | "aar" | "settings";

const FALLBACK_TIMEZONES = [
  "UTC", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
  "America/Anchorage", "Pacific/Honolulu", "Europe/London", "Europe/Berlin", "Europe/Paris",
  "Europe/Moscow", "Africa/Cairo", "Asia/Jerusalem", "Asia/Dubai", "Asia/Karachi",
  "Asia/Kolkata", "Asia/Bangkok", "Asia/Shanghai", "Asia/Tokyo", "Australia/Sydney",
  "Pacific/Auckland",
];

// Sort key for merging history events and session markers into one timeline.
export function timestampOf(e: LogEntry): string {
  return e.kind === "event" ? e.event.timestamp_start : e.marker.timestamp;
}

export function getTimezoneOptions(): string[] {
  try {
    const supported = (Intl as any).supportedValuesOf?.("timeZone");
    if (Array.isArray(supported) && supported.length) return supported;
  } catch {
    // fall through to fallback list
  }
  return FALLBACK_TIMEZONES;
}

const _fmtCache = new Map<string, Intl.DateTimeFormat>();

// Format a stored UTC timestamp in the configured display timezone (§3.8). Used
// for the AAR session list; falls back to a bare slice if the browser rejects
// the timezone name.
export function fmtDateTime(iso: string, tz: string): string {
  try {
    let fmt = _fmtCache.get(tz);
    if (!fmt) {
      fmt = new Intl.DateTimeFormat([], {
        timeZone: tz,
        year: "numeric", month: "short", day: "2-digit",
        hour: "2-digit", minute: "2-digit", hour12: false,
      });
      _fmtCache.set(tz, fmt);
    }
    return fmt.format(new Date(iso));
  } catch {
    return iso.slice(0, 16).replace("T", " ");
  }
}

const _MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// The running log's stamp — "03 Aug 26 03:30:34" — split so the row can drop the
// date when the viewport can't spare the width (see .logstamp__date in
// styles.css).
export function fmtLogStamp(iso: string, tz: string): { date: string; time: string } {
  try {
    const key = `${tz}|parts`;
    let fmt = _fmtCache.get(key);
    if (!fmt) {
      // hourCycle h23 rather than hour12:false — the latter still renders
      // midnight as "24" under some locales, which would read as tomorrow.
      fmt = new Intl.DateTimeFormat("en-GB", {
        timeZone: tz,
        year: "2-digit", month: "short", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
        hourCycle: "h23",
      });
      _fmtCache.set(key, fmt);
    }
    const parts = fmt.formatToParts(new Date(iso)).reduce(
      (acc, part) => {
        acc[part.type] = part.value;
        return acc;
      },
      {} as Record<string, string>,
    );
    return {
      date: `${parts.day ?? ""} ${parts.month ?? ""} ${parts.year ?? ""}`,
      time: `${parts.hour ?? ""}:${parts.minute ?? ""}:${parts.second ?? ""}`,
    };
  } catch {
    // Browser rejected the timezone name: fall back to the stored UTC text,
    // reshaped to the same layout so the column doesn't change form.
    return {
      date: `${iso.slice(8, 10)} ${_MONTHS[Number(iso.slice(5, 7)) - 1] ?? "???"} ${iso.slice(2, 4)}`,
      time: iso.slice(11, 19),
    };
  }
}
