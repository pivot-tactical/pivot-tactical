import { describe, it, expect } from 'vitest';

import {
  MAX_FREQ_HZ,
  MIN_FREQ_HZ,
  TUNE_GRID_HZ,
  formatMHz,
  netKey,
  regionFor,
  snapToGrid,
  stepHzFor,
  steppedFrom,
} from './freq';

describe('snapToGrid', () => {
  it('snaps to the 100 Hz tuning grid', () => {
    expect(TUNE_GRID_HZ).toBe(100);
    expect(snapToGrid(145_500_000)).toBe(145_500_000); // already on grid
    expect(snapToGrid(145_500_040)).toBe(145_500_000); // down to nearest
    expect(snapToGrid(145_500_060)).toBe(145_500_100); // up to nearest
  });

  it('leaves frequencies off the 12.5 kHz channel raster alone', () => {
    // The bug this replaced: 5.687 landed on 5.6875, 8.974 on 8.975, and so on.
    for (const mhz of [5.687, 8.974, 11.235, 13.206]) {
      expect(snapToGrid(mhz * 1e6)).toBeCloseTo(mhz * 1e6, 3);
    }
  });

  it('clamps to the tunable range', () => {
    expect(snapToGrid(1_000)).toBe(MIN_FREQ_HZ);
    expect(snapToGrid(4e9)).toBe(MAX_FREQ_HZ);
  });
});

describe('steppedFrom', () => {
  it('steps HF in 1 kHz', () => {
    expect(stepHzFor(7e6)).toBe(1_000);
    expect(steppedFrom(7_000_000, 1)).toBe(7_001_000);
    expect(steppedFrom(7_000_000, -1)).toBe(6_999_000);
  });

  it('steps VHF/UHF in the 12.5 kHz channel spacing', () => {
    expect(stepHzFor(145.5e6)).toBe(12_500);
    expect(steppedFrom(145_500_000, 1)).toBe(145_512_500);
    expect(steppedFrom(420_000_000, -1)).toBe(419_987_500);
  });

  it('steps onto the next multiple from an off-step frequency', () => {
    // From 5.6874 the next step up is the 1 kHz point above it, not +1 kHz from
    // where it happens to sit.
    expect(steppedFrom(5_687_400, 1)).toBe(5_688_000);
    expect(steppedFrom(5_687_400, -1)).toBe(5_686_000);
  });

  it('stays inside the tunable range at the edges', () => {
    expect(steppedFrom(MIN_FREQ_HZ, -1)).toBe(MIN_FREQ_HZ);
    expect(steppedFrom(MAX_FREQ_HZ, 1)).toBe(MAX_FREQ_HZ);
  });
});

describe('netKey', () => {
  it('groups only radios on the same 100 Hz point', () => {
    expect(netKey(5_687_000)).toBe(netKey(5_687_040));
    expect(netKey(5_687_000)).not.toBe(netKey(5_687_100));
    // A neighbour a few kHz away is a different net — and so must not pick up
    // that net's scenario override.
    expect(netKey(14_250_000)).not.toBe(netKey(14_254_000));
  });
});

describe('formatMHz', () => {
  it('renders the grid exactly at 4 dp', () => {
    expect(formatMHz(5_687_000)).toBe('5.6870');
    expect(formatMHz(145_512_500)).toBe('145.5125');
  });

  it('handles edge cases like zero, negatives, and large values', () => {
    expect(formatMHz(0)).toBe('0.0000');
    expect(formatMHz(-0)).toBe('0.0000');
    expect(formatMHz(-5_687_000)).toBe('-5.6870');
    expect(formatMHz(3_000_000_000)).toBe('3000.0000');
  });
});

describe('regionFor', () => {
  it('labels the ITU bands, upper edge belonging to the lower band', () => {
    expect(regionFor(7e6)).toBe('HF');
    expect(regionFor(30e6)).toBe('HF');
    expect(regionFor(145.5e6)).toBe('VHF');
    expect(regionFor(300e6)).toBe('VHF');
    expect(regionFor(420e6)).toBe('UHF');
  });
});
