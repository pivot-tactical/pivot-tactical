import { describe, it, expect, beforeEach, vi } from 'vitest';

describe('api token management', () => {
  describe('initialization', () => {
    beforeEach(() => {
      vi.resetModules();
      sessionStorage.clear();
    });

    it('should initialize token flag from sessionStorage', async () => {
      sessionStorage.setItem('pivot_instructor', '1');
      const api = await import('./api');
      expect(api.getToken()).toBe('cookie');
    });
  });

  describe('setToken and getToken', () => {
    beforeEach(async () => {
      sessionStorage.clear();
      const api = await import('./api');
      api.setToken(null);
    });

    it('should set the token flag and persist to sessionStorage', async () => {
      const api = await import('./api');
      const testToken = 'test-token-123';
      api.setToken(testToken);

      expect(api.getToken()).toBe('cookie');
      expect(sessionStorage.getItem('pivot_instructor')).toBe('1');
    });

    it('should clear the token flag and remove from sessionStorage when passing null', async () => {
      const api = await import('./api');
      const testToken = 'test-token-456';
      api.setToken(testToken);

      // Clear token
      api.setToken(null);

      expect(api.getToken()).toBeNull();
      expect(sessionStorage.getItem('pivot_instructor')).toBeNull();
    });

    it('should return null when token is not set', async () => {
      const api = await import('./api');
      expect(api.getToken()).toBeNull();
    });
  });
});
