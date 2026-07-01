import { describe, it, expect, beforeEach, vi } from 'vitest';

describe('api token management', () => {
  describe('initialization', () => {
    beforeEach(() => {
      vi.resetModules();
      sessionStorage.clear();
    });

    it('should initialize token from sessionStorage', async () => {
      sessionStorage.setItem('pivot_token', 'initial-token');
      const api = await import('./api');
      expect(api.getToken()).toBe('initial-token');
    });
  });

  describe('setToken and getToken', () => {
    beforeEach(async () => {
      sessionStorage.clear();
      const api = await import('./api');
      api.setToken(null);
    });

    it('should set the token and persist to sessionStorage', async () => {
      const api = await import('./api');
      const testToken = 'test-token-123';
      api.setToken(testToken);

      expect(api.getToken()).toBe(testToken);
      expect(sessionStorage.getItem('pivot_token')).toBe(testToken);
    });

    it('should clear the token and remove from sessionStorage when passing null', async () => {
      const api = await import('./api');
      const testToken = 'test-token-456';
      api.setToken(testToken);

      // Clear token
      api.setToken(null);

      expect(api.getToken()).toBeNull();
      expect(sessionStorage.getItem('pivot_token')).toBeNull();
    });

    it('should return null when token is not set', async () => {
      const api = await import('./api');
      expect(api.getToken()).toBeNull();
    });
  });
});
