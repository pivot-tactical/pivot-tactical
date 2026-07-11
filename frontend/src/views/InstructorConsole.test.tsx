import { render, screen, cleanup, act, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { InstructorConsole } from './InstructorConsole';
import { api } from '../api';

// A fully-formed EventRow for the running log, overridable per test.
function makeEvent(overrides: Record<string, unknown> = {}) {
  return {
    event_id: 'evt-1',
    trainee_name: 'ALPHA',
    frequency: '14.250 MHz',
    band_region: 'HF',
    tx_mode: 'Plain',
    audibility: 'Heard',
    sync_status: 'Completed',
    jammed: false,
    snr_db: 20,
    timestamp_start: '2026-06-05T12:00:00+00:00',
    duration_ms: 1500,
    transcription: 'helo wrld',
    transcription_confidence: 0.4,
    transcription_status: 'Done',
    transcription_original: null,
    transcription_edited: false,
    ...overrides,
  };
}

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
    editTranscription: vi.fn(),
    eventAudioUrl: vi.fn().mockReturnValue('blob:audio'),
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

  it('single-click on a transcript opens a freeform edit box and saves the correction', async () => {
    (api.recentEvents as any).mockResolvedValueOnce([makeEvent()]);
    (api.editTranscription as any).mockResolvedValueOnce(
      makeEvent({ transcription: 'hello world', transcription_original: 'helo wrld', transcription_edited: true })
    );

    await act(async () => {
      render(<InstructorConsole timezone="UTC" mustChangePassword={false} onTimezone={vi.fn()} onLogout={vi.fn()} />);
    });

    // Single click turns the transcript into an editable box.
    const cell = await screen.findByText('helo wrld');
    await act(async () => { fireEvent.click(cell); });
    const box = screen.getByLabelText('Edit transcript') as HTMLTextAreaElement;
    expect(box).toBeInTheDocument();

    await act(async () => {
      fireEvent.change(box, { target: { value: 'hello world' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    });

    expect(api.editTranscription).toHaveBeenCalledWith('evt-1', 'hello world');
  });

  it('Escape cancels an edit without calling the API', async () => {
    (api.recentEvents as any).mockResolvedValueOnce([makeEvent()]);

    await act(async () => {
      render(<InstructorConsole timezone="UTC" mustChangePassword={false} onTimezone={vi.fn()} onLogout={vi.fn()} />);
    });

    const cell = await screen.findByText('helo wrld');
    await act(async () => { fireEvent.click(cell); });
    const box = screen.getByLabelText('Edit transcript');
    await act(async () => {
      fireEvent.change(box, { target: { value: 'something else' } });
      fireEvent.keyDown(box, { key: 'Escape' });
    });

    expect(api.editTranscription).not.toHaveBeenCalled();
    expect(screen.getByText('helo wrld')).toBeInTheDocument();
  });

  it('an already-edited transcript highlights changes (char-level for fixes, whole word for rewords) with an edited badge', async () => {
    // "grid 123456 to cat" -> "grid 123556 to dog":
    //  - a single-digit fix (123456 -> 123556) should highlight only the digit,
    //    NOT the whole number token;
    //  - an unrelated reword (cat -> dog) should highlight the whole word;
    //  - unchanged words (grid, to) should not be highlighted at all.
    (api.recentEvents as any).mockResolvedValueOnce([
      makeEvent({
        transcription: 'grid 123556 to dog',
        transcription_original: 'grid 123456 to cat',
        transcription_edited: true,
      }),
    ]);

    let container: HTMLElement;
    await act(async () => {
      const r = render(<InstructorConsole timezone="UTC" mustChangePassword={false} onTimezone={vi.fn()} onLogout={vi.fn()} />);
      container = r.container;
    });

    // The "edited" badge is shown, and the full corrected text is rendered.
    expect(await screen.findByText(/edited/)).toBeInTheDocument();
    const cell = container!.querySelector('.transcript') as HTMLElement;
    expect(cell.textContent).toContain('grid 123556 to dog');

    const marks = Array.from(container!.querySelectorAll('.transcript__edit')).map((m) => m.textContent);
    const highlighted = marks.join('|');
    // Character-level: the changed digit is marked, but not the whole number.
    expect(highlighted).toContain('5');
    expect(marks).not.toContain('123556');
    // Whole-word: the reworded token is marked in full.
    expect(marks).toContain('dog');
    // Unchanged words are never highlighted.
    expect(highlighted).not.toContain('grid');
    expect(marks).not.toContain('to');
  });
});
