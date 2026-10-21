import { render, screen, cleanup, waitFor, fireEvent, act } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import App from './App';
import { api, setToken, getToken } from './api';
import { ApiError } from './errors';

vi.mock('./views/InstructorConsole', () => ({ InstructorConsole: ({ onLogout }: { onLogout: () => void }) =>
  <div>Restored instructor console<button onClick={onLogout}>Log out</button></div> }));

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>();
  return {
    ...actual,
    api: {
      status: vi.fn().mockResolvedValue({ display_timezone: 'UTC' }),
      refreshToken: vi.fn(),
      logout: vi.fn(),
      loginInstructor: vi.fn(),
    },
    getToken: vi.fn(),
    setToken: vi.fn(),
  };
});

describe('App', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(getToken).mockReturnValue(null);
    vi.mocked(api.status).mockResolvedValue({ display_timezone: 'UTC' } as never);
    vi.mocked(api.refreshToken).mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('clears token and falls back to login on session restore error', async () => {
    vi.mocked(getToken).mockReturnValue('cookie');
    vi.mocked(api.refreshToken).mockRejectedValue(new ApiError(401, 'Unauthorized'));

    render(<App />);

    await waitFor(() => {
      expect(setToken).toHaveBeenCalledWith(null);
    });

    await waitFor(() => {
      expect(screen.getByText(/Log in as instructor/i)).toBeInTheDocument();
    });
  });

  it('handles api.status rejection without crashing and renders login view', async () => {
    vi.mocked(api.status).mockRejectedValueOnce(new Error('Network error'));

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText(/Log in as instructor/i)).toBeInTheDocument();
    });
  });

  it('keeps a valid session during a network failure and restores it on retry', async () => {
    vi.mocked(getToken).mockReturnValue('cookie');
    vi.mocked(api.refreshToken)
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({ token: 'cookie' });

    render(<App />);
    expect(screen.getByRole('status')).toHaveTextContent('Restoring your session');
    expect(await screen.findByRole('alert')).toHaveTextContent('Cannot reach the server');
    expect(setToken).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Retry session' }));
    expect(await screen.findByText('Restored instructor console')).toBeInTheDocument();
    expect(setToken).not.toHaveBeenCalled();
  });

  it('allows returning to sign in after a failed restore without discarding the session', async () => {
    vi.mocked(getToken).mockReturnValue('cookie');
    vi.mocked(api.refreshToken).mockRejectedValueOnce(new ApiError(503, 'Unavailable'));
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: 'Back to sign in' }));
    expect(screen.getByText(/Log in as instructor/i)).toBeInTheDocument();
    expect(setToken).not.toHaveBeenCalled();
  });

  it('retains the instructor view after failed sign out and retries from Log out', async () => {
    vi.mocked(getToken).mockReturnValue('cookie');
    vi.mocked(api.refreshToken).mockResolvedValueOnce({ token: null });
    vi.mocked(api.logout).mockRejectedValueOnce(new TypeError('Failed to fetch')).mockResolvedValueOnce(undefined);
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: 'Log out' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Sign out failed');
    expect(screen.getByText('Restored instructor console')).toBeInTheDocument();
    expect(setToken).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Log out' }));
    expect(await screen.findByRole('button', { name: /Log in as instructor/ })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('ignores a refresh failure from an instructor session that has already signed out', async () => {
    const intervals = vi.spyOn(window, 'setInterval');
    let rejectRefresh!: (error: Error) => void;
    vi.mocked(getToken).mockReturnValue('cookie');
    vi.mocked(api.refreshToken).mockResolvedValueOnce({ token: null })
      .mockReturnValueOnce(new Promise((_resolve, reject) => { rejectRefresh = reject; }));
    vi.mocked(api.logout).mockResolvedValueOnce(undefined);
    vi.mocked(api.loginInstructor).mockResolvedValueOnce({ role: 'instructor', token: null });
    render(<App />);
    await screen.findByRole('button', { name: 'Log out' });
    const refresh = intervals.mock.calls.find((call) => call[1] === 15 * 60 * 1000)![0] as () => void;
    act(() => refresh());
    fireEvent.click(screen.getByRole('button', { name: 'Log out' }));
    fireEvent.click(await screen.findByRole('button', { name: /Log in as instructor/ }));
    fireEvent.change(screen.getByPlaceholderText('default: instructor'), { target: { value: 'secret' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign In' }));
    await screen.findByText('Restored instructor console');
    await act(async () => { rejectRefresh(new ApiError(401, 'Expired')); });
    expect(screen.getByText('Restored instructor console')).toBeInTheDocument();
    expect(setToken).not.toHaveBeenCalledWith(null);
  });
});
