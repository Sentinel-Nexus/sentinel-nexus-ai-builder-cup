// Fetch wrappers. Errors carry a message that is safe to show to an analyst.
export class ApiError extends Error {
  constructor(status, message, retryAfter) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

const UNREACHABLE = 'The service is not reachable. Check your connection and retry.';

async function parse(res) {
  if (res.ok) return res.json();
  let msg = `${res.status} ${res.statusText}`;
  try {
    const body = await res.json();
    if (body && body.detail) msg = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
  } catch { /* not JSON */ }
  throw new ApiError(res.status, msg, Number(res.headers.get('Retry-After')) || null);
}

const memo = new Map();

export async function get(path, { fresh = false } = {}) {
  if (!fresh && memo.has(path)) return memo.get(path);
  let res;
  try {
    res = await fetch(path, { headers: { Accept: 'application/json' } });
  } catch {
    throw new ApiError(0, UNREACHABLE);
  }
  const data = await parse(res);
  memo.set(path, data);
  return data;
}

export const forget = (prefix = '') => {
  for (const k of [...memo.keys()]) if (k.startsWith(prefix)) memo.delete(k);
};

export async function post(path, body) {
  let res;
  try {
    res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body || {}),
    });
  } catch {
    throw new ApiError(0, UNREACHABLE);
  }
  return parse(res);
}

/** POST that streams server-sent events. Calls onEvent(name, data); resolves when the stream ends. */
export async function stream(path, onEvent, signal) {
  let res;
  try {
    res = await fetch(path, { method: 'POST', headers: { Accept: 'text/event-stream' }, signal });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError(0, UNREACHABLE);
  }
  if (!res.ok) await parse(res);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let name = 'message';
      const data = [];
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) name = line.slice(6).trim();
        else if (line.startsWith('data:')) data.push(line.slice(5).trim());
      }
      if (data.length) {
        try { onEvent(name, JSON.parse(data.join('\n'))); } catch { /* ignore a malformed block */ }
      }
    }
  }
}
