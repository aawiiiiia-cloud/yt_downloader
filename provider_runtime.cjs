'use strict';
// First-party preload: record durations only, never request/response contents.
const http = require('node:http');
const { performance } = require('node:perf_hooks');
const started = performance.now(), cpu = process.cpuUsage();
const marker = Symbol.for('media-dl.provider-timing');
if (!http.Server.prototype[marker]) {
  const listen = http.Server.prototype.listen;
  const report = row => {
    try { process.stdout.write('MEDIA_DL_PROVIDER_TIMING ' + JSON.stringify(row) + '\n'); } catch {}
  };
  http.Server.prototype.listen = function (...args) {
    if (!Object.prototype.hasOwnProperty.call(this, marker)) {
      this[marker] = true;
      this.once('listening', () => {
        const elapsed = process.cpuUsage(cpu);
        report({ stage: 'node-ready', seconds: (performance.now() - started) / 1000,
          cpuSeconds: (elapsed.user + elapsed.system) / 1e6 });
      });
      this.on('request', (request, response) => {
        if (request.method !== 'POST' || request.url !== '/get_pot') return;
        const tokenStarted = performance.now();
        const tokenCPU = process.cpuUsage();
        response.once('finish', () => report({ stage: 'token', seconds: (performance.now() - tokenStarted) / 1000,
          cpuSeconds: (() => { const elapsed=process.cpuUsage(tokenCPU); return (elapsed.user+elapsed.system)/1e6; })(),
          status: response.statusCode }));
      });
    }
    return listen.apply(this, args);
  };
  http.Server.prototype[marker] = true;
}
