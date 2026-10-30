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

    it('should clear the token flag and remove from sessionStorage when passing empty string', async () => {
      const api = await import('./api');
      const testToken = 'test-token-789';
      api.setToken(testToken);

      // Clear token with empty string
      api.setToken('');

      expect(api.getToken()).toBeNull();
      expect(sessionStorage.getItem('pivot_instructor')).toBeNull();
    });

    it('should return null when token is not set', async () => {
      const api = await import('./api');
      expect(api.getToken()).toBeNull();
    });
  });
});

describe('api methods - updates, events, exports, and recordings', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(async () => {
    vi.resetModules();
    sessionStorage.clear();
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  it('checkUpdates calls GET /api/admin/updates/check', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ current_version: '1.0.0', channel: 'stable', available: [] }),
    });

    const { api } = await import('./api');
    await api.checkUpdates();

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/updates/check', {
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it('refreshUpdates calls POST /api/admin/updates/refresh', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ current_version: '1.0.0', channel: 'stable', available: [] }),
    });

    const { api } = await import('./api');
    await api.refreshUpdates();

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/updates/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it('applyUpdate sends POST /api/admin/updates/apply', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ staged: true, tag: 'v1.1.0', restart_required: true }),
    });

    const { api } = await import('./api');
    await api.applyUpdate('v1.1.0', 'http://a.com', 'http://sha.com', 'http://sig.com', 'asset.zip');

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/updates/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tag: 'v1.1.0',
        asset_url: 'http://a.com',
        sha256_url: 'http://sha.com',
        sig_url: 'http://sig.com',
        asset_name: 'asset.zip',
      }),
    });
  });

  it('rollbackUpdate sends POST /api/admin/updates/rollback with or without tag', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ staged: true, tag: 'v1.0.0', rollback: true, restart_required: true }),
    });

    const { api } = await import('./api');
    await api.rollbackUpdate('v1.0.0');
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/admin/updates/rollback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tag: 'v1.0.0' }),
    });

    await api.rollbackUpdate();
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/admin/updates/rollback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tag: null }),
    });
  });

  it('retainedVersions fetches GET /api/admin/updates/retained', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ retained: [], current_version: '1.0.0' }),
    });

    const { api } = await import('./api');
    await api.retainedVersions();

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/updates/retained', {
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it('deleteRetained sends DELETE /api/admin/updates/retained/:tag', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ deleted: 'v1.0.0', retained: [] }),
    });

    const { api } = await import('./api');
    await api.deleteRetained('v1.0.0/test');

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/updates/retained/v1.0.0%2Ftest', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it('restartServer sends POST /api/admin/restart', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ restarting: true, mode: 'service', staged: null }),
    });

    const { api } = await import('./api');
    await api.restartServer();
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/admin/restart', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force: false }),
    });

    await api.restartServer(true);
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/admin/restart', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force: true }),
    });
  });

  it('sessions fetches GET /api/sessions', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });

    const { api } = await import('./api');
    await api.sessions();

    expect(fetchMock).toHaveBeenCalledWith('/api/sessions', {
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it('recentEvents fetches GET /api/events/recent with limit', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => [],
    });

    const { api } = await import('./api');
    await api.recentEvents();
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/events/recent?limit=200', {
      headers: { 'Content-Type': 'application/json' },
    });

    await api.recentEvents(50);
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/events/recent?limit=50', {
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it('events fetches GET /api/sessions/:sessionId/events', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });

    const { api } = await import('./api');
    await api.events('sess-1');

    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/sess-1/events', {
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it('editTranscription sends POST /api/events/:eventId/transcription', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: 'e1', text: 'corrected' }),
    });

    const { api } = await import('./api');
    await api.editTranscription('e1', 'corrected');

    expect(fetchMock).toHaveBeenCalledWith('/api/events/e1/transcription', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: 'corrected' }),
    });
  });

  it('eventAudioUrl and exportUrl construct correct URL strings', async () => {
    const { api } = await import('./api');
    const audioUrl = api.eventAudioUrl('ev123', 'clean', 'plain');
    expect(audioUrl).toContain('/api/events/ev123/audio?mode=clean&view=plain&_');

    const expUrl = api.exportUrl('sess123', 'zip');
    expect(expUrl).toBe('/api/sessions/sess123/export?fmt=zip');
  });

  it('exportSession fetches session export blob and triggers download', async () => {
    const fakeBlob = new Blob(['test-export'], { type: 'application/zip' });
    fetchMock.mockResolvedValueOnce({
      ok: true,
      blob: async () => fakeBlob,
    });

    const createObjectURLMock = vi.fn().mockReturnValue('blob:test');
    const revokeObjectURLMock = vi.fn();

    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: createObjectURLMock,
      revokeObjectURL: revokeObjectURLMock,
    });

    const { api } = await import('./api');
    await api.exportSession('sess-1', 'zip', 'Special / Session: 1?');

    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/sess-1/export?fmt=zip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    expect(createObjectURLMock).toHaveBeenCalledWith(fakeBlob);
    expect(revokeObjectURLMock).toHaveBeenCalledWith('blob:test');
  });

  it('exportSession throws error on non-ok HTTP response', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 404,
      text: async () => 'Session not found',
    });

    const { api } = await import('./api');
    await expect(api.exportSession('sess-missing', 'csv')).rejects.toThrow('404: Session not found');
  });

  it('recordingsLocation fetches GET /api/admin/recordings/location', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ path: '/tmp/rec', exists: true }),
    });

    const { api } = await import('./api');
    await api.recordingsLocation();

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/recordings/location', {
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it('openRecordingsFolder calls POST /api/admin/recordings/open', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ opened: true, path: '/tmp/rec' }),
    });

    const { api } = await import('./api');
    await api.openRecordingsFolder();

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/recordings/open', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
  });
});

describe('api methods - radio control and session configuration', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(async () => {
    vi.resetModules();
    sessionStorage.clear();
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  it('tune sends POST /api/radio/tune', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ radio_id: 'r1', frequency: '121.500' }),
    });

    const { api } = await import('./api');
    await api.tune('r1', '121.500');

    expect(fetchMock).toHaveBeenCalledWith('/api/radio/tune', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ radio_id: 'r1', frequency: '121.500' }),
    });
  });

  it('setMode sends POST /api/radio/mode', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ radio_id: 'r1', mode: 'AM' }),
    });

    const { api } = await import('./api');
    await api.setMode('r1', 'AM');

    expect(fetchMock).toHaveBeenCalledWith('/api/radio/mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ radio_id: 'r1', mode: 'AM' }),
    });
  });

  it('instructorRadios fetches GET /api/admin/instructor-radios', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => [{ radio_id: 'ir1' }],
    });

    const { api } = await import('./api');
    const res = await api.instructorRadios();

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/instructor-radios', {
      headers: { 'Content-Type': 'application/json' },
    });
    expect(res).toEqual([{ radio_id: 'ir1' }]);
  });

  it('addInstructorRadio sends POST /api/admin/instructor-radios with optional params', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ radio_id: 'ir2' }),
    });

    const { api } = await import('./api');
    await api.addInstructorRadio('123.450', 'TOWER');

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/instructor-radios', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ frequency: '123.450', label: 'TOWER' }),
    });
  });

  it('addInstructorRadio handles missing optional params', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ radio_id: 'ir2' }),
    });

    const { api } = await import('./api');
    await api.addInstructorRadio();

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/instructor-radios', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ frequency: null, label: null }),
    });
  });

  it('removeInstructorRadio sends DELETE /api/admin/instructor-radios/:id', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: 'ok' }),
    });

    const { api } = await import('./api');
    await api.removeInstructorRadio('ir1');

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/instructor-radios/ir1', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it('tuneInstructorRadio sends POST /api/admin/instructor-radios/:id/tune', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ radio_id: 'ir1', frequency: '30.000' }),
    });

    const { api } = await import('./api');
    await api.tuneInstructorRadio('ir1', '30.000');

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/instructor-radios/ir1/tune', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ frequency: '30.000' }),
    });
  });

  it('modeInstructorRadio sends POST /api/admin/instructor-radios/:id/mode', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ radio_id: 'ir1', mode: 'FM' }),
    });

    const { api } = await import('./api');
    await api.modeInstructorRadio('ir1', 'FM');

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/instructor-radios/ir1/mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: 'FM' }),
    });
  });

  it('startSession sends POST /api/admin/session/start', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: 's1' }),
    });

    const { api } = await import('./api');
    const res = await api.startSession('Training Alpha');

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/session/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'Training Alpha' }),
    });
    expect(res).toEqual({ id: 's1' });
  });

  it('endSession sends POST /api/admin/session/end', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: 'ended' }),
    });

    const { api } = await import('./api');
    await api.endSession();

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/session/end', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it('terminals fetches GET /api/admin/terminals', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ session_active: true, session_id: 's1', session_name: 'Alpha', terminals: [] }),
    });

    const { api } = await import('./api');
    await api.terminals();

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/terminals', {
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it('scenario sends POST /api/admin/scenario', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: 'ok' }),
    });

    const payload = { preset: 'heavy_jamming' };
    const { api } = await import('./api');
    await api.scenario(payload);

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/scenario', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  });

  it('bandProfile fetches GET /api/band-profile', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ crypto_enabled: false, net_scenarios: [] }),
    });

    const { api } = await import('./api');
    await api.bandProfile();

    expect(fetchMock).toHaveBeenCalledWith('/api/band-profile', {
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it('getConfig fetches GET /api/admin/config', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ key: 'val' }),
    });

    const { api } = await import('./api');
    await api.getConfig();

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/config', {
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it('updateSettings sends POST /api/admin/settings', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ applied: { volume: 80 } }),
    });

    const { api } = await import('./api');
    await api.updateSettings({ volume: 80 });

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ volume: 80 }),
    });
  });
});

describe('api methods - auth and status', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(async () => {
    vi.resetModules();
    sessionStorage.clear();
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  it('status fetches /api/status', async () => {
    const mockStatus = { name: 'PIVOT', version: '1.0.0', git_sha: '123', session_active: false, terminals: 1, display_timezone: 'UTC' };
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => mockStatus,
    });

    const { api } = await import('./api');
    const res = await api.status();

    expect(fetchMock).toHaveBeenCalledWith('/api/status', {
      headers: { 'Content-Type': 'application/json' },
    });
    expect(res).toEqual(mockStatus);
  });

  it('throws error when jsonFetch encounters non-ok HTTP status', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 401,
      text: async () => 'Unauthorized',
    });

    const { api } = await import('./api');
    await expect(api.status()).rejects.toThrow('401: Unauthorized');
  });

  it('loginTrainee sends POST /api/login with trainee role and parameters', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: 'ok', token: 'dummy' }),
    });

    const { api } = await import('./api');
    await api.loginTrainee('ALPHA-1', 'T123');

    expect(fetchMock).toHaveBeenCalledWith('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role: 'trainee', name: 'ALPHA-1', trainee_id: 'T123' }),
    });
  });

  it('loginTrainee defaults trainee_id to null when omitted', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: 'ok', token: 'dummy' }),
    });

    const { api } = await import('./api');
    await api.loginTrainee('ALPHA-1');

    expect(fetchMock).toHaveBeenCalledWith('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role: 'trainee', name: 'ALPHA-1', trainee_id: null }),
    });
  });

  it('loginInstructor sends POST /api/login and sets token if present', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: 'ok', token: 'instr-token' }),
    });

    const { api, getToken } = await import('./api');
    const res = await api.loginInstructor('secret');

    expect(fetchMock).toHaveBeenCalledWith('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role: 'instructor', password: 'secret' }),
    });
    expect(res).toEqual({ status: 'ok', token: 'instr-token' });
    expect(getToken()).toBe('cookie');
  });

  it('refreshToken calls POST /api/auth/refresh and updates token status', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ token: 'refreshed-token', must_change_password: false }),
    });

    const { api, getToken } = await import('./api');
    const res = await api.refreshToken();

    expect(fetchMock).toHaveBeenCalledWith('/api/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    expect(res).toEqual({ token: 'refreshed-token', must_change_password: false });
    expect(getToken()).toBe('cookie');
  });

  it('logout calls POST /api/logout and clears token even if request fails', async () => {
    const { api, setToken, getToken } = await import('./api');
    setToken('active-token');
    expect(getToken()).toBe('cookie');

    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => 'Server Error',
    });

    await expect(api.logout()).rejects.toThrow('500: Server Error');
    expect(getToken()).toBeNull();
  });

  it('changePassword sends POST /api/admin/password with passwords', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: 'ok' }),
    });

    const { api } = await import('./api');
    await api.changePassword('oldpass', 'newpass');

    expect(fetchMock).toHaveBeenCalledWith('/api/admin/password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password: 'oldpass', new_password: 'newpass' }),
    });
  });
});
