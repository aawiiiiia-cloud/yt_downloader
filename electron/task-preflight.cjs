'use strict';
// Bound preparation waits without silently bypassing system proxies or credentials.
function waitForPreparation(operation, { signal, timeoutMs = 30000, label = '下载准备' } = {}) {
  return new Promise((resolve, reject) => {
    let settled = false, timer;
    const finish = (ok, value) => {
      if (settled) return;
      settled = true; clearTimeout(timer); signal?.removeEventListener('abort', abort);
      ok ? resolve(value) : reject(value);
    };
    const abort = () => finish(false, Object.assign(new Error('操作已取消'), { cancelled: true }));
    if (signal?.aborted) { abort(); return; }
    signal?.addEventListener('abort', abort, { once: true });
    timer = setTimeout(() => finish(false, new Error(`${label}等待超时，请检查网络或代理设置后重试`)), timeoutMs);
    // Observe late failures too: abandoned Chromium promises must not become unhandled.
    Promise.resolve().then(() => {
      if (!settled) return operation();
    }).then(value => finish(true, value), error => finish(false, error));
  });
}
module.exports = { waitForPreparation };
