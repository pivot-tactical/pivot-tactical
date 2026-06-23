import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { PivotSocket } from './ws';

describe('PivotSocket', () => {
  let MockWebSocket: any;
  let wsInstances: any[];

  beforeEach(() => {
    vi.useFakeTimers();
    wsInstances = [];

    MockWebSocket = vi.fn(() => {
      const instance = {
        send: vi.fn(),
        close: vi.fn(),
        readyState: WebSocket.OPEN,
      };
      wsInstances.push(instance);
      return instance;
    });

    // Make WebSocket.OPEN available on the mock class
    MockWebSocket.OPEN = WebSocket.OPEN;

    vi.stubGlobal('WebSocket', MockWebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('builds URL correctly from query object', () => {
    const socket = new PivotSocket({ foo: 'bar' });
    socket.connect();
    expect(MockWebSocket).toHaveBeenCalledWith(expect.stringContaining('foo=bar'));
    expect(wsInstances.length).toBe(1);
  });

  it('builds URL correctly from query function', () => {
    let q = { dynamic: '1' };
    const socket = new PivotSocket(() => q);
    socket.connect();
    expect(MockWebSocket).toHaveBeenCalledWith(expect.stringContaining('dynamic=1'));

    q = { dynamic: '2' };
    socket.connect();
    expect(MockWebSocket).toHaveBeenCalledWith(expect.stringContaining('dynamic=2'));
  });

  it('handles message events and triggers handlers', () => {
    const socket = new PivotSocket({});
    socket.connect();
    const ws = wsInstances[0];

    const handler = vi.fn();
    socket.on('test_event', handler);

    ws.onmessage({ data: JSON.stringify({ type: 'test_event', payload: { data: 123 } }) });

    expect(handler).toHaveBeenCalledWith({ data: 123 });
  });

  it('handles binary audio messages', () => {
    const socket = new PivotSocket({});
    socket.connect();
    const ws = wsInstances[0];

    const audioHandler = vi.fn();
    socket.onAudio(audioHandler);

    const buffer = new ArrayBuffer(8);
    ws.onmessage({ data: buffer });

    expect(audioHandler).toHaveBeenCalledWith(buffer);
  });

  it('ignores malformed JSON messages', () => {
    const socket = new PivotSocket({});
    socket.connect();
    const ws = wsInstances[0];

    const handler = vi.fn();
    socket.on('test_event', handler);

    ws.onmessage({ data: 'invalid json' });

    expect(handler).not.toHaveBeenCalled();
  });

  it('starts heartbeat on open and clears on close', () => {
    const socket = new PivotSocket({});
    socket.connect();
    const ws = wsInstances[0];

    ws.onopen();

    vi.advanceTimersByTime(10000);
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: 'heartbeat', payload: {} }));

    ws.onclose();
    ws.send.mockClear();

    vi.advanceTimersByTime(10000);
    expect(ws.send).not.toHaveBeenCalled();
  });

  it('reconnects after close unless disconnected', () => {
    const socket = new PivotSocket({});
    socket.connect();
    const ws = wsInstances[0];

    ws.onclose();
    expect(wsInstances.length).toBe(1);

    vi.advanceTimersByTime(1500);
    expect(wsInstances.length).toBe(2);
  });

  it('does not reconnect if explicitly disconnected', () => {
    const socket = new PivotSocket({});
    socket.connect();
    const ws = wsInstances[0];

    socket.disconnect();
    ws.onclose();

    vi.advanceTimersByTime(1500);
    expect(wsInstances.length).toBe(1);
  });

  it('sends audio if socket is open', () => {
    const socket = new PivotSocket({});
    socket.connect();
    const ws = wsInstances[0];

    const buffer = new ArrayBuffer(8);
    socket.sendAudio(buffer);

    expect(ws.send).toHaveBeenCalledWith(buffer);
  });

  it('does not send audio if socket is not open', () => {
    const socket = new PivotSocket({});
    socket.connect();
    const ws = wsInstances[0];
    ws.readyState = WebSocket.CONNECTING;

    const buffer = new ArrayBuffer(8);
    socket.sendAudio(buffer);

    expect(ws.send).not.toHaveBeenCalled();
  });

  it('sends action messages', () => {
    const socket = new PivotSocket({});
    socket.connect();
    const ws = wsInstances[0];

    socket.tune('123.450');
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: 'tune', payload: { frequency: '123.450' } }));

    socket.pttStart('123.450', 'AM');
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: 'ptt_start', payload: { frequency: '123.450', tx_mode: 'AM' } }));

    socket.pttEnd();
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: 'ptt_end', payload: {} }));

    socket.pttAbort();
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: 'ptt_abort', payload: {} }));

    socket.modeChange('AM');
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: 'mode_change', payload: { mode: 'AM' } }));

    socket.instrPttStart('instr-1', '123.450', 'AM');
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: 'instr_ptt_start', payload: { radio_id: 'instr-1', frequency: '123.450', tx_mode: 'AM' } }));

    socket.instrPttEnd('instr-1');
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: 'instr_ptt_end', payload: { radio_id: 'instr-1' } }));

    socket.instrPttAbort('instr-1');
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: 'instr_ptt_abort', payload: { radio_id: 'instr-1' } }));

    socket.instrTune('instr-1', '123.450');
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: 'instr_tune', payload: { radio_id: 'instr-1', frequency: '123.450' } }));

    socket.instrMode('instr-1', 'AM');
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: 'instr_mode', payload: { radio_id: 'instr-1', mode: 'AM' } }));

    socket.instrRxNoise('instr-1', true);
    expect(ws.send).toHaveBeenCalledWith(JSON.stringify({ type: 'instr_rx_noise', payload: { radio_id: 'instr-1', enabled: true } }));
  });

  it('unsubscribes handlers correctly', () => {
    const socket = new PivotSocket({});
    socket.connect();
    const ws = wsInstances[0];

    const handler = vi.fn();
    const unsubscribe = socket.on('test_event', handler);

    unsubscribe();
    ws.onmessage({ data: JSON.stringify({ type: 'test_event', payload: { data: 123 } }) });

    expect(handler).not.toHaveBeenCalled();
  });
});
