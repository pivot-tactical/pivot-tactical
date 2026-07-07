import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

import { Radio } from './Radio';
import { PivotSocket } from '../ws';
import type { LoginResponse } from '../types';
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
  SignalMeter: () => <div data-testid="signal-meter" />,
  METER_DECAY: 0.9,
}));

vi.mock('../components/VolumeSlider', () => ({
  VolumeSlider: ({ value, onChange }: any) => (
    <input type="range" data-testid="volume-slider" value={value} onChange={(e) => onChange(Number(e.target.value))} />
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
  playClick: vi.fn(),
  playSyncTone: vi.fn(),
}));

describe('Radio', () => {
  let mockSocket: any;
  let mockLogin: LoginResponse;

  beforeEach(() => {
    mockSocket = {
      onAudio: vi.fn(),
      on: vi.fn().mockReturnValue(() => {}),
      sendAudio: vi.fn(),
      pttStart: vi.fn(),
      pttEnd: vi.fn(),
      pttAbort: vi.fn(),
      tune: vi.fn(),
      modeChange: vi.fn(),
    };
    mockLogin = {
      role: 'trainee',
      radio_id: 'radio1',
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
    expect(screen.getByTestId('clock')).toHaveTextContent('UTC');

    // Check initial socket setups
    expect(mockSocket.onAudio).toHaveBeenCalled();
    expect(mockSocket.on).toHaveBeenCalledWith('tuned', expect.any(Function));
  });

  it('handles tuning up and down', () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);

    const decBtn = screen.getByLabelText('Decrease frequency');
    const incBtn = screen.getByLabelText('Increase frequency');

    fireEvent.click(incBtn);
    expect(mockSocket.tune).toHaveBeenCalledWith('7.0125 MHz');
    expect(screen.getByText('7.0125')).toBeInTheDocument();

    fireEvent.click(decBtn);
    fireEvent.click(decBtn);
    expect(mockSocket.tune).toHaveBeenCalledWith('6.9875 MHz');
    expect(screen.getByText('6.9875')).toBeInTheDocument();
  });

  it('handles manual frequency entry', () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);

    const entryInput = screen.getByLabelText('Frequency in MHz');
    fireEvent.change(entryInput, { target: { value: '8.5' } });
    fireEvent.keyDown(entryInput, { key: 'Enter' });

    expect(mockSocket.tune).toHaveBeenCalledWith('8.5000 MHz');
  });

  it('handles tuning boundaries', () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    const entryInput = screen.getByLabelText('Frequency in MHz');

    // Test lower bound (1.6e6)
    fireEvent.change(entryInput, { target: { value: '1.0' } });
    fireEvent.keyDown(entryInput, { key: 'Enter' });
    expect(mockSocket.tune).toHaveBeenCalledWith('1.6000 MHz');

    // Test upper bound (3e9)
    fireEvent.change(entryInput, { target: { value: '4000.0' } });
    fireEvent.keyDown(entryInput, { key: 'Enter' });
    expect(mockSocket.tune).toHaveBeenCalledWith('3000.0000 MHz');
  });

  it('handles mode toggle', () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);

    const dial = screen.getByTestId('mode-dial');
    fireEvent.click(dial);

    expect(mockSocket.modeChange).toHaveBeenCalledWith('Cypher');
    expect(dial).toHaveTextContent('Cypher');
  });

  it('handles PTT via button', async () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);

    const pttBtn = screen.getByRole('button', { name: /push to talk/i });

    // Start PTT
    await act(async () => {
      fireEvent.mouseDown(pttBtn);
    });

    expect(mockAudioIO.startCapture).toHaveBeenCalled();
    expect(mockSocket.pttStart).toHaveBeenCalledWith('7.0000 MHz', 'Plain');

    // End PTT
    fireEvent.mouseUp(pttBtn);
    expect(mockAudioIO.stopCapture).toHaveBeenCalled();
    expect(mockSocket.pttEnd).toHaveBeenCalled();
  });

  it('handles PTT via spacebar', async () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);

    // Space down
    await act(async () => {
      fireEvent.keyDown(window, { code: 'Space' });
    });

    expect(mockSocket.pttStart).toHaveBeenCalledWith('7.0000 MHz', 'Plain');

    // Space up
    fireEvent.keyUp(window, { code: 'Space' });
    expect(mockSocket.pttEnd).toHaveBeenCalled();
  });

  it('ignores PTT via spacebar when typing in an input', async () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    const entryInput = screen.getByLabelText('Frequency in MHz');

    await act(async () => {
      fireEvent.keyDown(entryInput, { code: 'Space', target: entryInput });
    });

    expect(mockSocket.pttStart).not.toHaveBeenCalled();
  });

  it('aborts PTT when releasing during CRYPTO_SYNC', async () => {
    const handlers: Record<string, Function> = {};
    mockSocket.on.mockImplementation((event: string, handler: Function) => {
      handlers[event] = handler;
      return () => {};
    });

    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    const pttBtn = screen.getByRole('button', { name: /push to talk/i });

    // Start PTT and simulate sync
    await act(async () => {
      fireEvent.mouseDown(pttBtn);
    });
    act(() => {
      handlers['ptt_started']({ sync_applies: true });
    });
    expect(screen.getByRole('button', { name: /crypto sync…/i })).toBeInTheDocument();

    // Release PTT
    fireEvent.mouseUp(pttBtn);

    expect(mockSocket.pttAbort).toHaveBeenCalled();
    expect(mockSocket.pttEnd).not.toHaveBeenCalled();
  });

  it('handles onMouseLeave during PTT', async () => {
    const handlers: Record<string, Function> = {};
    mockSocket.on.mockImplementation((event: string, handler: Function) => {
      handlers[event] = handler;
      return () => {};
    });
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    const pttBtn = screen.getByRole('button', { name: /push to talk/i });

    await act(async () => {
      fireEvent.mouseDown(pttBtn);
    });
    expect(mockSocket.pttStart).toHaveBeenCalled();
    act(() => { handlers['ptt_started']({ sync_applies: false }); });

    fireEvent.mouseLeave(pttBtn);
    expect(mockSocket.pttEnd).toHaveBeenCalled();
  });

  it('responds to websocket state updates correctly', () => {
    const handlers: Record<string, Function> = {};
    mockSocket.on.mockImplementation((event: string, handler: Function) => {
      handlers[event] = handler;
      return () => {};
    });

    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);

    act(() => { handlers['tuned']({ frequency_hz: 8000000 }); });
    expect(screen.getByText('8.0000')).toBeInTheDocument();

    act(() => { handlers['mode_changed']({ mode: 'Cypher' }); });
    expect(screen.getByTestId('mode-dial')).toHaveTextContent('Cypher');

    act(() => { handlers['ptt_started']({ sync_applies: false }); });
    expect(screen.getByRole('button', { name: /tx/i })).toBeInTheDocument();

    act(() => { handlers['ptt_started']({ sync_applies: true }); });
    expect(screen.getByRole('button', { name: /crypto sync…/i })).toBeInTheDocument();

    act(() => { handlers['secure_tx']({}); });
    expect(screen.getByRole('button', { name: /secure tx/i })).toBeInTheDocument();

    act(() => { handlers['ptt_ended']({}); });
    expect(screen.getByRole('button', { name: /push to talk/i })).toBeInTheDocument();

    // Test that aborting also returns to IDLE state
    act(() => { handlers['ptt_started']({ sync_applies: true }); });
    expect(screen.getByRole('button', { name: /crypto sync…/i })).toBeInTheDocument();
    act(() => { handlers['ptt_aborted']({}); });
    expect(screen.getByRole('button', { name: /push to talk/i })).toBeInTheDocument();
  });

  it('updates volume correctly', () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);
    const slider = screen.getByTestId('volume-slider');

    fireEvent.change(slider, { target: { value: '0.8' } });
    expect(audioModule.saveVolume).toHaveBeenCalledWith('trainee', 0.8);
  });

  it('ignores e.repeat on spacebar keydown', async () => {
    render(<Radio socket={mockSocket as PivotSocket} login={mockLogin} timezone="UTC" />);

    await act(async () => {
      fireEvent.keyDown(window, { code: 'Space', repeat: true });
    });

    expect(mockSocket.pttStart).not.toHaveBeenCalled();
  });
});
