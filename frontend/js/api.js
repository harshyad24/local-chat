'use strict';

/* Thin wrapper around fetch for the backend API. */
const API = {
  async json(method, url, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const r = await fetch(url, opts);
    if (!r.ok) {
      let detail = r.statusText;
      try { detail = (await r.json()).detail || detail; } catch (_) { /* ignore */ }
      throw new Error(detail);
    }
    return r.json();
  },
  get: (u) => API.json('GET', u),
  post: (u, b) => API.json('POST', u, b === undefined ? {} : b),
  put: (u, b) => API.json('PUT', u, b),
  patch: (u, b) => API.json('PATCH', u, b),
  del: (u) => API.json('DELETE', u),

  /** POST and read a newline-delimited JSON stream, calling onEvent for each object. */
  async stream(url, body, signal, onEvent) {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    });
    if (!r.ok) {
      let detail = r.statusText;
      try { detail = (await r.json()).detail || detail; } catch (_) { /* ignore */ }
      throw new Error(detail);
    }
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        if (line) onEvent(JSON.parse(line));
      }
    }
    const rest = buf.trim();
    if (rest) onEvent(JSON.parse(rest));
  },
};
