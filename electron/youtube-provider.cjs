'use strict';
const { pythonTask } = require('./bridge.cjs');

function providerUrl(value) {
  const match = typeof value === 'string' && value.match(/^http:\/\/127\.0\.0\.1:(\d{1,5})$/);
  return match && Number(match[1]) > 0 && Number(match[1]) <= 65535 ? value : null;
}

// The application owns this service. Cancelling one extraction must not stop it
// underneath another extraction or download.
class YouTubeProvider {
  constructor({ task = pythonTask, track = handle => handle, startupTimeout = 90000 } = {}) {
    this.makeTask = task; this.track = track; this.startupTimeout = startupTimeout;
    this.instance = null; this.listeners = new Set(); this.closed = false;
  }
  start() {
    const instance = { url: null };
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
          for (const listener of this.listeners) { try { listener(type, data); } catch {} }
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
      const instance = this.instance || this.start();
      if (instance.url) return instance.url;
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
module.exports = { YouTubeProvider, providerUrl };
