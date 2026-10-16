import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import {
  traineeId,
  saveTrainee,
  touchTrainee,
  loadTrainee,
  TRAINEE_NAME_KEY,
  TRAINEE_SEEN_KEY,
  TRAINEE_TTL_MS,
} from './traineeStorage';

describe('traineeStorage', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('traineeId', () => {
    it('returns existing ID if already in localStorage', () => {
      localStorage.setItem('pivot_trainee_id', 'existing-uuid-1234');
      expect(traineeId()).toBe('existing-uuid-1234');
    });

    it('generates a new UUID and stores it if not present', () => {
      const id = traineeId();
      expect(id).toBeTruthy();
      expect(localStorage.getItem('pivot_trainee_id')).toBe(id);
    });
  });

  describe('saveTrainee and loadTrainee', () => {
    it('saves callsign and timestamp', () => {
      const now = 1700000000000;
      vi.setSystemTime(now);

      saveTrainee('Alpha1');
      expect(localStorage.getItem(TRAINEE_NAME_KEY)).toBe('Alpha1');
      expect(localStorage.getItem(TRAINEE_SEEN_KEY)).toBe(String(now));
    });

    it('loads active trainee within TTL', () => {
      const now = 1700000000000;
      vi.setSystemTime(now);

      saveTrainee('Bravo2');
      expect(loadTrainee()).toBe('Bravo2');

      // Advance time by 30 minutes (less than 1 hr TTL)
      vi.setSystemTime(now + 30 * 60 * 1000);
      expect(loadTrainee()).toBe('Bravo2');
    });

    it('returns null if trainee timestamp exceeds TTL', () => {
      const now = 1700000000000;
      vi.setSystemTime(now);

      saveTrainee('Charlie3');

      // Advance time by 61 minutes (exceeds TRAINEE_TTL_MS)
      vi.setSystemTime(now + TRAINEE_TTL_MS + 60000);
      expect(loadTrainee()).toBeNull();
    });

    it('returns null if no trainee is stored', () => {
      expect(loadTrainee()).toBeNull();
    });
  });

  describe('touchTrainee', () => {
    it('updates seen timestamp if trainee name is set', () => {
      const startTime = 1700000000000;
      vi.setSystemTime(startTime);

      saveTrainee('Delta4');

      const updatedTime = startTime + 10 * 60 * 1000;
      vi.setSystemTime(updatedTime);

      touchTrainee();
      expect(localStorage.getItem(TRAINEE_SEEN_KEY)).toBe(String(updatedTime));
    });

    it('does not set seen timestamp if trainee name is not set', () => {
      touchTrainee();
      expect(localStorage.getItem(TRAINEE_SEEN_KEY)).toBeNull();
    });
  });
});
