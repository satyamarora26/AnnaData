import run from './Gemini';
import { TextDecoder, TextEncoder } from 'util';

describe('optional browser location', () => {
  const originalGeolocation = navigator.geolocation;
  const originalFetch = global.fetch;
  const originalDecoder = global.TextDecoder;

  beforeEach(() => {
    jest.useFakeTimers();
    global.TextDecoder = TextDecoder;
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ answer: 'Source-backed answer' }),
    });
  });

  afterEach(() => {
    jest.useRealTimers();
    global.fetch = originalFetch;
    global.TextDecoder = originalDecoder;
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true, value: originalGeolocation,
    });
  });

  test('sends the question within one second even when GPS never responds', async () => {
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: { getCurrentPosition: jest.fn() },
    });
    const answer = run('What is PM-KISAN?', []);
    jest.advanceTimersByTime(1000);
    await Promise.resolve();
    await Promise.resolve();
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({
      query: 'What is PM-KISAN?', history: [],
    });
    expect(await answer).toBe('Source-backed answer');
  });

  test('includes coordinates when they arrive within the location budget', async () => {
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true,
      value: {
        getCurrentPosition: success => setTimeout(() => success({
          coords: { latitude: 30.9, longitude: 75.5 },
        }), 200),
      },
    });
    const answer = run('Will it rain?', []);
    jest.advanceTimersByTime(200);
    await answer;
    expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toMatchObject({
      latitude: 30.9, longitude: 75.5,
    });
    expect(jest.getTimerCount()).toBe(0);
  });

  function streamResponse(text) {
    const bytes = new TextEncoder().encode(text);
    let offset = 0;
    return {
      ok: true,
      headers: { get: () => 'text/event-stream; charset=utf-8' },
      body: { getReader: () => ({
        read: async () => offset < bytes.length
          ? { value: bytes.slice(offset, ++offset), done: false }
          : { done: true },
        cancel: jest.fn().mockResolvedValue(),
        releaseLock: jest.fn(),
      }) },
    };
  }

  test('delivers progress before the final answer across split UTF-8 frames', async () => {
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true, value: { getCurrentPosition: (_, fail) => fail() },
    });
    global.fetch.mockResolvedValue(streamResponse(
      ': keep-alive\n\ndata: {"type":"status","stage":"retrieving"}\n\n' +
      'data: {"type":"result","answer":"Verified ₹6,000"}\n\n'
    ));
    const progress = jest.fn();
    const answer = await run('PM-KISAN?', [], progress);
    expect(progress).toHaveBeenCalledWith('retrieving');
    expect(answer).toBe('Verified ₹6,000');
  });

  test.each([
    ['data: {"type":"status","stage":"composing"}\n\n', /ended before/],
    ['data: {"type":"error","detail":"Agent is temporarily unavailable"}\n\n', /temporarily unavailable/],
  ])('recovers when the stream fails instead of returning a partial answer', async (frames, expected) => {
    Object.defineProperty(navigator, 'geolocation', {
      configurable: true, value: { getCurrentPosition: (_, fail) => fail() },
    });
    global.fetch.mockResolvedValue(streamResponse(frames));
    const log = jest.spyOn(console, 'error').mockImplementation(() => {});
    try {
      expect(await run('hello', [])).toMatch(expected);
    } finally {
      log.mockRestore();
    }
  });
});
