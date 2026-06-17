import { render, screen, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, it, expect, vi, afterEach, beforeEach, MockInstance } from 'vitest';
import { SignalMeter, METER_DECAY } from './SignalMeter';

describe('SignalMeter', () => {
  let rafSpy: MockInstance;
  let cafSpy: MockInstance;
  let callbacks: FrameRequestCallback[] = [];
  let rafId = 0;

  beforeEach(() => {
    callbacks = [];
    rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      callbacks.push(cb);
      return ++rafId;
    });
    cafSpy = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((_id) => {
      // no-op for mock
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  const runTick = () => {
    const cb = callbacks.shift();
    if (cb) {
      cb(performance.now());
    }
  };

  it('renders label correctly', () => {
    const read = vi.fn(() => 0.5);
    render(<SignalMeter label="SIG" read={read} />);
    expect(screen.getByText('SIG')).toBeInTheDocument();
  });

  it('polls the read function and updates fill style on animation frame', () => {
    const read = vi.fn(() => 0.5);
    const { container } = render(<SignalMeter label="SIG" read={read} />);

    // Initially read is not called
    expect(read).not.toHaveBeenCalled();

    // Run first animation frame
    runTick();

    // Read should have been called
    expect(read).toHaveBeenCalledTimes(1);

    // Get the fill element and check its style width
    const fill = container.querySelector('.signal__fill') as HTMLElement;
    expect(fill.style.width).toBe('50%');

    // Update read value and run next frame
    read.mockReturnValue(0.75);
    runTick();

    expect(read).toHaveBeenCalledTimes(2);
    expect(fill.style.width).toBe('75%');
  });

  it('clamps level between 0 and 1', () => {
    const read = vi.fn();
    const { container } = render(<SignalMeter label="SIG" read={read} />);
    const fill = container.querySelector('.signal__fill') as HTMLElement;

    // Test negative value
    read.mockReturnValue(-0.5);
    runTick();
    expect(fill.style.width).toBe('0%');

    // Test > 1 value
    read.mockReturnValue(1.5);
    runTick();
    expect(fill.style.width).toBe('100%');
  });

  it('cleans up requestAnimationFrame on unmount', () => {
    const read = vi.fn(() => 0.5);
    const { unmount } = render(<SignalMeter label="SIG" read={read} />);

    expect(rafSpy).toHaveBeenCalled();
    const lastRafId = rafSpy.mock.results[rafSpy.mock.results.length - 1].value;

    expect(cafSpy).not.toHaveBeenCalled();

    unmount();

    expect(cafSpy).toHaveBeenCalledWith(lastRafId);
  });

  it('exports METER_DECAY', () => {
    expect(METER_DECAY).toBeDefined();
    expect(typeof METER_DECAY).toBe('number');
  });
});
