'use strict';
const { contentKey } = require('./model.cjs');

// Cache and visible-page data get a short head start. Supplementing no longer
// waits for the entire page-data timeout; the first matching result wins.
async function resolveDouyinContext({ url, signal, cached, read, supplement, onSupplement = () => {},
  graceMs = 400, pageTimeoutMs = 7000, supplementTimeoutMs = 10000, pollMs = 200 }) {
  const id = contentKey(url)?.match(/^douyin:(\d+)$/)?.[1];
  if (!id || signal?.aborted) return null;
  const matches = value => value?.videoId === id && value.detail ? value : null;
  const initial = matches(cached());
  if (initial) return initial;
  const controller = new AbortController();
  const pause = (ms, taskSignal) => new Promise(resolve => {
    const finish = () => { clearTimeout(timer); taskSignal.removeEventListener('abort', finish); resolve(); };
    const timer = setTimeout(finish, ms);
    taskSignal.addEventListener('abort', finish, { once: true });
    if (taskSignal.aborted) finish();
  });
  let finish, pending = 2;
  const result = new Promise(resolve => { finish = resolve; });
  const stop = () => { finish(null); controller.abort(); };
  signal?.addEventListener('abort', stop, { once: true });
  const bounded = async (work, timeoutMs) => {
    const task = new AbortController();
    const abort = () => task.abort(); controller.signal.addEventListener('abort', abort, { once: true });
    let timeout, end;
    const interrupted = new Promise(resolve => {
      end = () => resolve(null); task.signal.addEventListener('abort', end, { once: true });
      timeout = setTimeout(abort, timeoutMs);
    });
    try {
      if (controller.signal.aborted) abort();
      return await Promise.race([Promise.resolve().then(() => task.signal.aborted ? null : work(task.signal)), interrupted]);
    } catch { return null; }
    finally {
      clearTimeout(timeout); controller.signal.removeEventListener('abort', abort);
      task.signal.removeEventListener('abort', end); task.abort();
    }
  };
  const page = bounded(async taskSignal => {
    while (!taskSignal.aborted) {
      const ready = matches(cached()); if (ready) return ready;
      try { const context = matches(await read(taskSignal)); if (context) return context; } catch {}
      if (!taskSignal.aborted) await pause(pollMs, taskSignal);
    }
    return null;
  }, pageTimeoutMs);
  const probe = bounded(async taskSignal => {
    await pause(graceMs, taskSignal);
    if (taskSignal.aborted) return null;
    const ready = matches(cached()); if (ready) return ready;
    onSupplement(); return supplement(taskSignal);
  }, graceMs + supplementTimeoutMs);
  for (const source of [page, probe]) source.then(value => {
    const context = matches(value);
    if (context && !controller.signal.aborted) { finish(context); controller.abort(); }
    else if (--pending === 0) finish(null);
  });
  try { if (signal?.aborted) stop(); return await result; }
  finally { signal?.removeEventListener('abort', stop); controller.abort(); }
}
module.exports = { resolveDouyinContext };
