// Frequency handling shared by the trainee radio and the instructor console
// (spec §3.1.2). Both views tune, step and display frequencies the same way, and
// both must agree with the server — so the grid lives here rather than being
// spelled out twice.

// Mirrors MIN_FREQ_HZ / MAX_FREQ_HZ in server/pivot/core/bands.py.
export const MIN_FREQ_HZ = 1.6e6;
export const MAX_FREQ_HZ = 3e9;

// The tuning grid, mirroring TUNING_STEP_HZ in server/pivot/core/bands.py.
// Radios dial to 100 Hz — the resolution a real set tunes in, and fine enough
// that a frequency handed out in an order (5.687, 8.974, 11.235 MHz) is reached
// exactly. It is not a channel raster: nets here are emergent, and the grid
// exists only so two operators who dialled the same frequency land together.
export const TUNE_GRID_HZ = 100;

/** Snap to the tuning grid and clamp to the tunable range. */
export function snapToGrid(hz: number): number {
  return Math.max(MIN_FREQ_HZ, Math.min(MAX_FREQ_HZ, Math.round(hz / TUNE_GRID_HZ) * TUNE_GRID_HZ));
}

/**
 * How far the ▼/▲ buttons nudge from `hz`. Typing gets you to any frequency on
 * the grid; these are for walking around one, so they use the increment the
 * band is actually worked in — 1 kHz on HF, where nets sit on odd frequencies,
 * and the 12.5 kHz channel spacing on VHF/UHF.
 */
export function stepHzFor(hz: number): number {
  return hz <= 30e6 ? 1_000 : 12_500;
}

/** The next frequency up (+1) or down (-1) from `hz`, on that band's step. */
export function steppedFrom(hz: number, direction: 1 | -1): number {
  const step = stepHzFor(hz);
  return snapToGrid((Math.round(hz / step) + direction) * step);
}

/** The radio display's fixed 4 dp form — the tuning grid rendered exactly. */
export function formatMHz(hz: number): string {
  return (hz / 1e6).toFixed(4);
}

/**
 * A frequency's net index. Radios quantised to the same index are on the same
 * emergent net, so this is also the key a per-net scenario override lands on —
 * matching `net_key_for` server-side.
 */
export function netKey(hz: number): number {
  return Math.round(hz / TUNE_GRID_HZ);
}

/**
 * ITU band label (ITU-R V.431): HF ≤30 MHz, VHF ≤300 MHz, UHF above — the upper
 * edge of each band belongs to the lower band, so 30 MHz is HF.
 */
export function regionFor(hz: number): string {
  return hz <= 30e6 ? "HF" : hz <= 300e6 ? "VHF" : "UHF";
}
