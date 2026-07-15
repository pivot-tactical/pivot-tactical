import { render, screen, cleanup, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import App from './App';
import { api, setToken, getToken } from './api';

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>();
  return {
    ...actual,
    api: {
      status: vi.fn().mockResolvedValue({ display_timezone: 'UTC' }),
      refreshToken: vi.fn(),
    },
    getToken: vi.fn(),
    setToken: vi.fn(),
  };
});

describe('App', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('clears token and falls back to login on session restore error', async () => {
    vi.mocked(getToken).mockReturnValue('cookie');
    vi.mocked(api.refreshToken).mockRejectedValue(new Error('401 Unauthorized'));

    render(<App />);

    await waitFor(() => {
      expect(setToken).toHaveBeenCalledWith(null);
    });

    await waitFor(() => {
      expect(screen.getByText(/Log in as instructor/i)).toBeInTheDocument();
    });
  });
});
