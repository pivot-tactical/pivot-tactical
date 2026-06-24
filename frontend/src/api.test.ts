import { describe, it, expect, beforeEach } from 'vitest';
import { setToken, getToken } from './api';

describe('api token management', () => {
  beforeEach(() => {
    sessionStorage.clear();
    // Reset internal login state via setToken(null)
    setToken(null);
  });

  it('should set the login state and persist the flag to sessionStorage', () => {
    setToken('active');

    expect(getToken()).toBe('authenticated');
    expect(sessionStorage.getItem('pivot_session')).toBe('1');
  });

  it('should clear the login state and remove the flag from sessionStorage when passing null', () => {
    setToken('active');

    // Clear login state
    setToken(null);

    expect(getToken()).toBeNull();
    expect(sessionStorage.getItem('pivot_session')).toBeNull();
  });
});
