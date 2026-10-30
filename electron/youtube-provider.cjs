'use strict';
const { pythonTask } = require('./bridge.cjs');

function providerUrl(value) {
  const match = typeof value === 'string' && value.match(/^http:\/\/127\.0\.0\.1:(\d{1,5})$/);
  return match && Number(match[1]) > 0 && Number(match[1]) <= 65535 ? value : null;
}

function providerTiming(row) {
  if (!row || !['validation', 'node-ready', 'service-ready', 'token', 'worker-ready'].includes(row.stage) ||
      typeof row.seconds !== 'number' || !Number.isFinite(row.seconds) || row.seconds < 0 || row.seconds > 3600) return null;
  const safe = { stage: row.stage, seconds: row.seconds };
  for (const key of ['cpuSeconds', 'nodeSeconds', 'importSeconds', 'prepareSeconds'])
    if (typeof row[key] === 'number' && Number.isFinite(row[key]) && row[key] >= 0 && row[key] <= 3600) safe[key] = row[key];
  if (Number.isInteger(row.status) && row.status >= 100 && row.status <= 599) safe.status = row.status;
  if (Number.isInteger(row.attempt) && row.attempt >= 1 && row.attempt <= 3) safe.attempt = row.attempt;
  if (typeof row.optimized === 'boolean') safe.optimized = row.optimized;
  return safe;
}

// The application owns this service. Cancelling one extraction must not stop it
// underneath another extraction or download.
class YouTubeProvider {
  constructor({ task = pythonTask, track = handle => handle, startupTimeout = 90000, onTiming = () => {} } = {}) {
    this.makeTask = task; this.track = track; this.startupTimeout = startupTimeout;
    this.instance = null; this.listeners = new Set(); this.closed = false;
    this.onTiming = onTiming;
  }
  start() {
    if (this.closed) return null;
    if (this.instance) return this.instance;
    const instance = { url: null, logs: [] };
    instance.ready = new Promise(resolve => { instance.resolve = resolve; });
    this.instance = instance;
    const finish = () => {
      clearTimeout(instance.timer); instance.resolve(null);
      if (this.instance === instance) this.instance = null;
    };
    try {
      instance.handle = this.track(this.makeTask({ command: 'provider' }, (type, data) => {
        if (this.instance !== instance || this.closed) return;
        if (type === 'provider-ready') {
          instance.url = providerUrl(data?.url);
          clearTimeout(instance.timer); instance.resolve(instance.url);
        } else if (type === 'log') {
          if (typeof data !== 'string') return;
          if (!instance.url) instance.logs = [...instance.logs, data.slice(0, 1000)].slice(-12);
          for (const listener of this.listeners) { try { listener(type, data); } catch {} }
        } else if (type === 'provider-timing') {
          const row = providerTiming(data);
          if (row) { try { this.onTiming(row); } catch {} }
        }
      }));
      instance.handle.promise.then(finish, finish);
      if (!instance.url) {
        instance.timer = setTimeout(() => { instance.resolve(null); instance.handle.cancel(); }, this.startupTimeout);
        instance.timer.unref?.();
      }
    } catch { finish(); }
    return instance;
  }
  async ensure(onEvent = () => {}, signal) {
    if (this.closed || signal?.aborted) return null;
    this.listeners.add(onEvent);
    let abort;
    try {
      const existing = this.instance;
      const instance = existing || this.start();
      if (!instance) return null;
      if (instance.url) return instance.url;
      if (existing) for (const text of instance.logs) { try { onEvent('log', text); } catch {} }
      const aborted = new Promise(resolve => {
        abort = () => resolve(null);
        signal?.addEventListener('abort', abort, { once: true });
        if (signal?.aborted) abort();
      });
      return await Promise.race([instance.ready, aborted]);
    } finally {
      if (abort) signal?.removeEventListener('abort', abort);
      this.listeners.delete(onEvent);
    }
  }
  close() {
    this.closed = true;
    if (this.instance) {
      clearTimeout(this.instance.timer); this.instance.resolve(null); this.instance.handle?.cancel();
    }
    this.listeners.clear();
  }
}
module.exports = { YouTubeProvider, providerUrl, providerTiming };
