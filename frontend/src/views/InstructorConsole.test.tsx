import { render, screen, cleanup, act, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { InstructorConsole } from './InstructorConsole';

// Mock audio
vi.mock('../audio', () => ({
  AudioIO: vi.fn().mockImplementation(() => ({
    init: vi.fn().mockResolvedValue(undefined),
    prewarm: vi.fn().mockResolvedValue(undefined),
    startCapture: vi.fn().mockResolvedValue(undefined),
    stopCapture: vi.fn(),
    setVolume: vi.fn(),
    play: vi.fn(),
    resume: vi.fn(),
    pause: vi.fn(),
    close: vi.fn(),
    src: ''
  })),
  loadVolume: vi.fn(),
  saveVolume: vi.fn(),
  parseTaggedAudio: vi.fn(),
  pcmLevel: vi.fn(),
  playClick: vi.fn(),
  playSyncTone: vi.fn()
}));

// Mock api
vi.mock('../api', () => ({
  api: {
    status: vi.fn().mockResolvedValue({}),
    getUpdates: vi.fn().mockResolvedValue([]),
    checkUpdates: vi.fn().mockResolvedValue({ standing: 'current' }),
    getSettings: vi.fn().mockResolvedValue({}),
    instructorRadios: vi.fn().mockResolvedValue([]),
    recentEvents: vi.fn().mockResolvedValue([]),
    sessions: vi.fn().mockResolvedValue([]),
    getConfig: vi.fn().mockResolvedValue({}),
    bandProfile: vi.fn().mockResolvedValue({ crypto_enabled: true }),
    terminals: vi.fn().mockResolvedValue({ session_active: false, terminals: [] }),
    refreshUpdates: vi.fn().mockResolvedValue({ standing: 'current' }),
    recordingsLocation: vi.fn().mockResolvedValue({ path: '/data/recordings', exists: true }),
    openRecordingsFolder: vi.fn().mockResolvedValue({ opened: true, path: '/data/recordings' }),
  },
  getToken: vi.fn().mockReturnValue('mock-token'),
}));

// Mock ws
vi.mock('../ws', () => {
  return {
    PivotSocket: vi.fn().mockImplementation(() => {
      let handlers: Record<string, Function[]> = {};
      return {
        on: vi.fn((event, handler) => {
          if (!handlers[event]) handlers[event] = [];
          handlers[event].push(handler);
          return vi.fn(); // return a cleanup function "off"
        }),
        onAudio: vi.fn(),
        connect: vi.fn(),
        disconnect: vi.fn(),
        send: vi.fn(),
        // helper to simulate socket events
        emit: (event: string, payload: any) => {
          if (handlers[event]) handlers[event].forEach(h => h(payload));
        }
      };
    })
  };
});

// Avoid offsetHeight/Width issues in jsdom by mocking
Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, value: 100 });
Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, value: 100 });

describe('InstructorConsole', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders settings tab when mustChangePassword is true', async () => {
    await act(async () => {
      render(<InstructorConsole timezone="UTC" mustChangePassword={true} onTimezone={vi.fn()} onLogout={vi.fn()} />);
    });

    expect(screen.getByText('Instructor Password')).toBeInTheDocument();
    expect(screen.getByText('You are using the default password. Please change it.')).toBeInTheDocument();
  });

  it('renders radios tab by default when mustChangePassword is false', async () => {
    await act(async () => {
      render(<InstructorConsole timezone="UTC" mustChangePassword={false} onTimezone={vi.fn()} onLogout={vi.fn()} />);
    });

    expect(screen.getByText('Running Event Log')).toBeInTheDocument();
  });

  it('can switch between tabs', async () => {
    await act(async () => {
      render(<InstructorConsole timezone="UTC" mustChangePassword={false} onTimezone={vi.fn()} onLogout={vi.fn()} />);
    });

    const monitorTabBtn = screen.getByRole('button', { name: /monitor/i });

    await act(async () => {
      fireEvent.click(monitorTabBtn);
    });

    expect(screen.getByText(/Connected Terminals/i)).toBeInTheDocument();

    const settingsTabBtn = screen.getByRole('button', { name: /settings/i });

    await act(async () => {
      fireEvent.click(settingsTabBtn);
    });

    expect(screen.getByText('Instructor Password')).toBeInTheDocument();
  });
});
