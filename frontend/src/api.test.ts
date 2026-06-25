import { describe, it, expect, beforeEach } from 'vitest';
import { setToken, getToken } from './api';

describe('api token management', () => {
  beforeEach(() => {
    sessionStorage.clear();
    // Reset internal token state via setToken(null)
    setToken(null);
  });

  it('should set the token and persist to sessionStorage', () => {
    const testToken = 'test-token-123';
    setToken(testToken);

    expect(getToken()).toBe(testToken);
    expect(sessionStorage.getItem('pivot_token')).toBe(testToken);
  });

  it('should clear the token and remove from sessionStorage when passing null', () => {
    const testToken = 'test-token-456';
    setToken(testToken);

    // Clear token
    setToken(null);

    expect(getToken()).toBeNull();
    expect(sessionStorage.getItem('pivot_token')).toBeNull();
  });
});
