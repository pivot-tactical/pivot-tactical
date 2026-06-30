import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { setToken, getToken } from './api';

describe('api token management', () => {
  beforeEach(() => {
    sessionStorage.clear();
    // Reset internal token state via setToken(null)
    setToken(null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('should set the token and persist to sessionStorage', () => {
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem');
    const testToken = 'test-token-123';
    setToken(testToken);

    expect(getToken()).toBe(testToken);
    expect(setItemSpy).toHaveBeenCalledWith('pivot_token', testToken);
    expect(sessionStorage.getItem('pivot_token')).toBe(testToken);
  });

  it('should clear the token and remove from sessionStorage when passing null', () => {
    const removeItemSpy = vi.spyOn(Storage.prototype, 'removeItem');
    const testToken = 'test-token-456';
    setToken(testToken);

    // Clear token
    setToken(null);

    expect(getToken()).toBeNull();
    expect(removeItemSpy).toHaveBeenCalledWith('pivot_token');
    expect(sessionStorage.getItem('pivot_token')).toBeNull();
  });

  it('should clear the token and remove from sessionStorage when passing an empty string', () => {
    const removeItemSpy = vi.spyOn(Storage.prototype, 'removeItem');
    const testToken = 'test-token-789';
    setToken(testToken);

    // Clear token with empty string
    setToken('');

    expect(getToken()).toBe('');
    expect(removeItemSpy).toHaveBeenCalledWith('pivot_token');
    expect(sessionStorage.getItem('pivot_token')).toBeNull();
  });
});
