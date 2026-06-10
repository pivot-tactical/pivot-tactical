import { describe, it, expect, beforeEach } from 'vitest';
import { setToken, getToken } from './api';

describe('api token management', () => {
  beforeEach(() => {
    localStorage.clear();
    // Reset internal token state via setToken(null)
    setToken(null);
  });

  it('should set the token and persist to localStorage', () => {
    const testToken = 'test-token-123';
    setToken(testToken);

    expect(getToken()).toBe(testToken);
    expect(localStorage.getItem('pivot_token')).toBe(testToken);
  });

  it('should clear the token and remove from localStorage when passing null', () => {
    const testToken = 'test-token-456';
    setToken(testToken);

    // Clear token
    setToken(null);

    expect(getToken()).toBeNull();
    expect(localStorage.getItem('pivot_token')).toBeNull();
  });
});
