import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

import { Radio } from './Radio';
import { PivotSocket } from '../ws';
import type { LoginResponse, RadioState } from '../types';
import * as audioModule from '../audio';

// Mock the components
vi.mock('../components/ModeDial', () => ({
  ModeDial: ({ mode, onToggle, disabled }: any) => (
    <button data-testid="mode-dial" disabled={disabled} onClick={onToggle}>
      {mode}
    </button>
  ),
}));

vi.mock('../components/SevenSegmentClock', () => ({
  SevenSegmentClock: ({ timezone }: any) => <div data-testid="clock">{timezone}</div>,
}));

vi.mock('../components/SignalMeter', () => ({
  SignalMeter: ({ label }: any) => <div data-testid="signal-meter">{label}</div>,
  METER_DECAY: 0.9,
}));

vi.mock('../components/VolumeSlider', () => ({
  VolumeSlider: ({ value, onChange, ariaLabel }: any) => (
    <input
      type="range"
      data-testid="volume-slider"
      aria-label={ariaLabel}
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
    />
  ),
}));

// Mock audio
const mockAudioIO = {
  setVolume: vi.fn(),
  init: vi.fn().mockResolvedValue(undefined),
  prewarm: vi.fn().mockResolvedValue(undefined),
  startCapture: vi.fn().mockResolvedValue(undefined),
  stopCapture: vi.fn(),
  play: vi.fn(),
  close: vi.fn(),
};

vi.mock('../audio', () => ({
  AudioIO: vi.fn(() => mockAudioIO),
  loadVolume: vi.fn(() => 0.5),
  saveVolume: vi.fn(),
  pcmLevel: vi.fn(() => 0.5),
  parseTaggedAudio: vi.fn(() => ({ radioId: 'radio1', pcm: new ArrayBuffer(4) })),
  playClick: vi.fn(),
  playSyncTone: vi.fn(),
}));

// A `radio_added` payload as the server sends it (a RadioState).
function addedRadio(overrides: Partial<RadioState> = {}): RadioState {
  return {
    radio_id: 'trainee-1#2',
    name: 'ALPHA/R2',
    is_instructor: false,
    frequency: '7.0000 MHz',
    frequency_hz: 7_000_000,
    band_region: 'HF',
    mode: 'Plain',
    status: 'idle',
    ...overrides,
  };
}

describe('Radio', () => {
  let mockSocket: any;
  let mockLogin: LoginResponse;
  let handlers: Record<string, Function>;

  const ptt = (name: string) => screen.getByLabelText(`Push to talk on ${name}`);

  beforeEach(() => {
    handlers = {};
    mockSocket = {
      onAudio: vi.fn(),
      on: vi.fn((event: string, handler: Function) => {
        handlers[event] = handler;
        return () => {};
      }),
      sendAudio: vi.fn(),
      pttStart: vi.fn(),
      pttEnd: vi.fn(),
      pttAbort: vi.fn(),
      tune: vi.fn(),
      modeChange: vi.fn(),
      addRadio: vi.fn(),
      removeRadio: vi.fn(),
    };
    mockLogin = {
      role: 'trainee',
      radio_id: 'radio1',
      name: 'ALPHA',
      frequency_hz: 7000000,
      mode: 'Plain',
    };
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders correctly with initial login props', () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);

    expect(screen.getByText(/ON NET/)).toBeInTheDocument();
    expect(screen.getByText('7.0000')).toBeInTheDocument();
    expect(screen.getByTestId('mode-dial')).toHaveTextContent('Plain');
    expect(screen.getByTestId('signal-meter')).toHaveTextContent('SIGNAL · HF');
    expect(screen.getByTestId('clock')).toHaveTextContent('UTC');

    // Check initial socket setups
    expect(mockSocket.onAudio).toHaveBeenCalled();
    expect(mockSocket.on).toHaveBeenCalledWith('tuned', expect.any(Function));
  });

  it('handles tuning up and down', () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);

    const decBtn = screen.getByLabelText('Decrease frequency on ALPHA');
    const incBtn = screen.getByLabelText('Increase frequency on ALPHA');

    fireEvent.click(incBtn);
    expect(mockSocket.tune).toHaveBeenCalledWith('7.0125 MHz', 'radio1');
    expect(screen.getByText('7.0125')).toBeInTheDocument();

    fireEvent.click(decBtn);
    fireEvent.click(decBtn);
    expect(mockSocket.tune).toHaveBeenCalledWith('6.9875 MHz', 'radio1');
    expect(screen.getByText('6.9875')).toBeInTheDocument();
  });

  it('handles manual frequency entry', () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);

    const entryInput = screen.getByLabelText('Frequency in MHz on ALPHA');
    fireEvent.change(entryInput, { target: { value: '8.5' } });
    fireEvent.keyDown(entryInput, { key: 'Enter' });

    expect(mockSocket.tune).toHaveBeenCalledWith('8.5000 MHz', 'radio1');
  });

  it('handles tuning boundaries', () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    const entryInput = screen.getByLabelText('Frequency in MHz on ALPHA');

    // Test lower bound (1.6e6)
    fireEvent.change(entryInput, { target: { value: '1.0' } });
    fireEvent.keyDown(entryInput, { key: 'Enter' });
    expect(mockSocket.tune).toHaveBeenCalledWith('1.6000 MHz', 'radio1');

    // Test upper bound (3e9)
    fireEvent.change(entryInput, { target: { value: '4000.0' } });
    fireEvent.keyDown(entryInput, { key: 'Enter' });
    expect(mockSocket.tune).toHaveBeenCalledWith('3000.0000 MHz', 'radio1');
  });

  it('handles mode toggle', () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);

    const dial = screen.getByTestId('mode-dial');
    fireEvent.click(dial);

    expect(mockSocket.modeChange).toHaveBeenCalledWith('Cypher', 'radio1');
    expect(dial).toHaveTextContent('Cypher');
  });

  it('handles PTT via button', async () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);

    const pttBtn = ptt('ALPHA');

    // Start PTT
    await act(async () => {
      fireEvent.mouseDown(pttBtn);
    });

    expect(mockAudioIO.startCapture).toHaveBeenCalled();
    expect(mockSocket.pttStart).toHaveBeenCalledWith('7.0000 MHz', 'Plain', 'radio1');

    // End PTT
    fireEvent.mouseUp(pttBtn);
    expect(mockAudioIO.stopCapture).toHaveBeenCalled();
    expect(mockSocket.pttEnd).toHaveBeenCalledWith('radio1');
  });

  it('handles PTT via the radio\'s numpad key', async () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    expect(ptt('ALPHA')).toHaveTextContent('HOLD · NUMPAD 1');

    // Numpad 1 down
    await act(async () => {
      fireEvent.keyDown(window, { code: 'Numpad1' });
    });

    expect(mockSocket.pttStart).toHaveBeenCalledWith('7.0000 MHz', 'Plain', 'radio1');

    // Numpad 1 up
    fireEvent.keyUp(window, { code: 'Numpad1' });
    expect(mockSocket.pttEnd).toHaveBeenCalledWith('radio1');
  });

  it('ignores the spacebar — every radio is keyed by its numpad key', async () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);

    await act(async () => {
      fireEvent.keyDown(window, { code: 'Space' });
    });

    expect(mockSocket.pttStart).not.toHaveBeenCalled();
  });

  it('ignores a numpad key with no radio behind it', async () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);

    await act(async () => {
      fireEvent.keyDown(window, { code: 'Numpad4' });
    });

    expect(mockSocket.pttStart).not.toHaveBeenCalled();
  });

  it('ignores PTT via the numpad when typing in an input', async () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    const entryInput = screen.getByLabelText('Frequency in MHz on ALPHA');

    await act(async () => {
      fireEvent.keyDown(entryInput, { code: 'Numpad1', target: entryInput });
    });

    expect(mockSocket.pttStart).not.toHaveBeenCalled();
  });

  it('aborts PTT when releasing during CRYPTO_SYNC', async () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    const pttBtn = ptt('ALPHA');

    // Start PTT and simulate sync
    await act(async () => {
      fireEvent.mouseDown(pttBtn);
    });
    act(() => {
      handlers['ptt_started']({ sync_applies: true });
    });
    expect(pttBtn).toHaveTextContent('CRYPTO SYNC…');

    // Release PTT
    fireEvent.mouseUp(pttBtn);

    expect(mockSocket.pttAbort).toHaveBeenCalledWith('radio1');
    expect(mockSocket.pttEnd).not.toHaveBeenCalled();
  });

  it('handles onMouseLeave during PTT', async () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    const pttBtn = ptt('ALPHA');

    await act(async () => {
      fireEvent.mouseDown(pttBtn);
    });
    expect(mockSocket.pttStart).toHaveBeenCalled();
    act(() => { handlers['ptt_started']({ sync_applies: false }); });

    fireEvent.mouseLeave(pttBtn);
    expect(mockSocket.pttEnd).toHaveBeenCalledWith('radio1');
  });

  it('responds to websocket state updates correctly', () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    const pttBtn = ptt('ALPHA');

    act(() => { handlers['tuned']({ frequency_hz: 8000000 }); });
    expect(screen.getByText('8.0000')).toBeInTheDocument();

    act(() => { handlers['mode_changed']({ mode: 'Cypher' }); });
    expect(screen.getByTestId('mode-dial')).toHaveTextContent('Cypher');

    act(() => { handlers['ptt_started']({ sync_applies: false }); });
    expect(pttBtn).toHaveTextContent('TX');

    act(() => { handlers['ptt_started']({ sync_applies: true }); });
    expect(pttBtn).toHaveTextContent('CRYPTO SYNC…');

    act(() => { handlers['secure_tx']({}); });
    expect(pttBtn).toHaveTextContent('SECURE TX');

    act(() => { handlers['ptt_ended']({}); });
    expect(pttBtn).toHaveTextContent('PUSH TO TALK');

    // Test that aborting also returns to IDLE state
    act(() => { handlers['ptt_started']({ sync_applies: true }); });
    expect(pttBtn).toHaveTextContent('CRYPTO SYNC…');
    act(() => { handlers['ptt_aborted']({}); });
    expect(pttBtn).toHaveTextContent('PUSH TO TALK');
  });

  it('updates volume correctly', () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    const slider = screen.getByTestId('volume-slider');

    fireEvent.change(slider, { target: { value: '0.8' } });
    expect(audioModule.saveVolume).toHaveBeenCalledWith('trainee', 0.8);
  });

  it('ignores e.repeat on a numpad keydown', async () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);

    await act(async () => {
      fireEvent.keyDown(window, { code: 'Numpad1', repeat: true });
    });

    expect(mockSocket.pttStart).not.toHaveBeenCalled();
  });

  // --- extra radios (§3.2.2) ---------------------------------------------- //

  it('asks for the next free slot when adding a radio, and shows what comes back', () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);

    fireEvent.click(screen.getByText('+ Add Radio'));
    expect(mockSocket.addRadio).toHaveBeenCalledWith(2);
    // Nothing is shown until the server confirms with the authoritative radio.
    expect(screen.queryByLabelText('Push to talk on ALPHA/R2')).not.toBeInTheDocument();

    act(() => { handlers['radio_added'](addedRadio({ frequency_hz: 145_500_000 })); });
    expect(ptt('ALPHA/R2')).toBeInTheDocument();
    expect(screen.getByText('145.5000')).toBeInTheDocument();

    // The next add takes the next free slot.
    fireEvent.click(screen.getByText('+ Add Radio'));
    expect(mockSocket.addRadio).toHaveBeenLastCalledWith(3);
  });

  it('tunes, keys and removes an added radio independently of the first', async () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    act(() => { handlers['radio_added'](addedRadio({ frequency_hz: 145_500_000 })); });

    // Tuning radio 2 leaves radio 1 where it was.
    fireEvent.click(screen.getByLabelText('Increase frequency on ALPHA/R2'));
    expect(mockSocket.tune).toHaveBeenCalledWith('145.5125 MHz', 'trainee-1#2');
    expect(screen.getByText('7.0000')).toBeInTheDocument();

    // Keying radio 2 keys only radio 2.
    await act(async () => {
      fireEvent.mouseDown(ptt('ALPHA/R2'));
    });
    expect(mockSocket.pttStart).toHaveBeenCalledWith('145.5125 MHz', 'Plain', 'trainee-1#2');
    act(() => { handlers['ptt_started']({ sync_applies: false, radio_id: 'trainee-1#2' }); });
    expect(ptt('ALPHA/R2')).toHaveTextContent('TX');
    expect(ptt('ALPHA')).toHaveTextContent('PUSH TO TALK');
    fireEvent.mouseUp(ptt('ALPHA/R2'));
    expect(mockSocket.pttEnd).toHaveBeenCalledWith('trainee-1#2');

    // The radio's own numpad key keys it too.
    expect(ptt('ALPHA/R2')).toHaveTextContent('HOLD · NUMPAD 2');
    await act(async () => {
      fireEvent.keyDown(window, { code: 'Numpad2' });
    });
    expect(mockSocket.pttStart).toHaveBeenLastCalledWith('145.5125 MHz', 'Plain', 'trainee-1#2');
    fireEvent.keyUp(window, { code: 'Numpad2' });
    expect(mockSocket.pttEnd).toHaveBeenLastCalledWith('trainee-1#2');

    fireEvent.click(screen.getByLabelText('Remove ALPHA/R2'));
    expect(mockSocket.removeRadio).toHaveBeenCalledWith('trainee-1#2');
    expect(screen.queryByLabelText('Push to talk on ALPHA/R2')).not.toBeInTheDocument();
  });

  it('never offers to remove the radio the terminal logged in with', () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    act(() => { handlers['radio_added'](addedRadio()); });

    expect(screen.queryByLabelText('Remove ALPHA')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Remove ALPHA/R2')).toBeInTheDocument();
  });

  it('re-declares its extra radios after the socket reconnects', () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    act(() => { handlers['radio_added'](addedRadio({ frequency_hz: 145_500_000, mode: 'Cypher' })); });

    act(() => { handlers['open']({}); });

    // Slot, frequency and mode, so the radio comes back exactly as shown.
    expect(mockSocket.addRadio).toHaveBeenCalledWith(2, '145.5000 MHz', 'Cypher');
  });

  // --- focus (mute every other radio) ------------------------------------- //

  it('mutes every other radio while one is focused, and unmutes on a second click', () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    act(() => { handlers['radio_added'](addedRadio()); });
    mockAudioIO.setVolume.mockClear();

    // Focus radio 2: radio 1 goes silent, radio 2 is left playing as it was.
    fireEvent.click(screen.getByLabelText('Focus ALPHA/R2'));
    expect(mockAudioIO.setVolume).toHaveBeenCalledWith(0, 'radio1');
    expect(mockAudioIO.setVolume).not.toHaveBeenCalledWith(0, 'trainee-1#2');
    expect(screen.getByLabelText('Focus ALPHA/R2')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('MUTED')).toBeInTheDocument();

    // Clicking it again brings the other radios (and their noise) back.
    mockAudioIO.setVolume.mockClear();
    fireEvent.click(screen.getByLabelText('Focus ALPHA/R2'));
    expect(mockAudioIO.setVolume).toHaveBeenCalledWith(0.5, 'radio1');
    expect(screen.queryByText('MUTED')).not.toBeInTheDocument();
  });

  it('moves focus straight from one radio to another', () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    act(() => { handlers['radio_added'](addedRadio()); });

    fireEvent.click(screen.getByLabelText('Focus ALPHA/R2'));
    mockAudioIO.setVolume.mockClear();
    fireEvent.click(screen.getByLabelText('Focus ALPHA'));

    expect(mockAudioIO.setVolume).toHaveBeenCalledWith(0.5, 'radio1');
    expect(mockAudioIO.setVolume).toHaveBeenCalledWith(0, 'trainee-1#2');
    expect(screen.getByLabelText('Focus ALPHA')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByLabelText('Focus ALPHA/R2')).toHaveAttribute('aria-pressed', 'false');
  });

  it('leaves the PTT keys alone when a radio is focused — focus only mutes', async () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    act(() => { handlers['radio_added'](addedRadio({ frequency_hz: 145_500_000 })); });
    fireEvent.click(screen.getByLabelText('Focus ALPHA/R2'));

    // Each numpad key still keys its own radio, focused or not.
    await act(async () => {
      fireEvent.keyDown(window, { code: 'Numpad1' });
    });
    expect(mockSocket.pttStart).toHaveBeenLastCalledWith('7.0000 MHz', 'Plain', 'radio1');
    fireEvent.keyUp(window, { code: 'Numpad1' });

    await act(async () => {
      fireEvent.keyDown(window, { code: 'Numpad2' });
    });
    expect(mockSocket.pttStart).toHaveBeenLastCalledWith('145.5000 MHz', 'Plain', 'trainee-1#2');
    fireEvent.keyUp(window, { code: 'Numpad2' });
    expect(mockSocket.pttEnd).toHaveBeenLastCalledWith('trainee-1#2');
  });

  it('routes received audio to the radio that heard it', () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    const onAudio = mockSocket.onAudio.mock.calls[0][0];

    vi.mocked(audioModule.parseTaggedAudio).mockReturnValueOnce({
      radioId: 'trainee-1#2',
      pcm: new ArrayBuffer(8),
    });
    onAudio(new ArrayBuffer(12));

    expect(mockAudioIO.play).toHaveBeenCalledWith(expect.any(ArrayBuffer), 'trainee-1#2');
  });
});
