'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const { YouTubeProvider, providerUrl } = require('../youtube-provider.cjs');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
function fixture(options = {}) {
  const calls = [];
  const provider = new YouTubeProvider({ ...options, task(request, emit) {
    let resolve, reject;
    const row = { request, emit, promise: new Promise((yes, no) => { resolve = yes; reject = no; }),
      end() { resolve({}); }, cancel() { row.cancelled = true; reject(new Error('cancelled')); } };
    calls.push(row); return row;
  } });
  return { provider, calls };
}
test('only a plain IPv4 loopback provider endpoint is accepted', () => {
  assert.equal(providerUrl('http://127.0.0.1:45001'), 'http://127.0.0.1:45001');
  for (const url of ['https://127.0.0.1:45001', 'http://localhost:45001', 'http://user@127.0.0.1:45001',
    'http://127.0.0.1:45001/path', 'http://127.0.0.1:0', 'http://127.0.0.1:65536', null]) assert.equal(providerUrl(url), null);
});
test('concurrent and successive tasks reuse one application-owned provider', async t => {
  const { provider, calls } = fixture(); t.after(() => provider.close());
  const a = provider.ensure(), b = provider.ensure();
  assert.equal(calls.length, 1); assert.equal(calls[0].request.command, 'provider');
  calls[0].emit('provider-ready', { url: 'http://127.0.0.1:45001' });
  assert.deepEqual(await Promise.all([a, b]), ['http://127.0.0.1:45001', 'http://127.0.0.1:45001']);
  assert.equal(await provider.ensure(), 'http://127.0.0.1:45001'); assert.equal(calls.length, 1);
});
test('cancelling one waiting parse releases its listener without cancelling another task', async t => {
  const { provider, calls } = fixture(); t.after(() => provider.close());
  const controller = new AbortController(), logs = [];
  const a = provider.ensure(() => logs.push('cancelled'), controller.signal);
  const b = provider.ensure(() => logs.push('active'));
  controller.abort(); assert.equal(await a, null); assert.ok(!calls[0].cancelled);
  calls[0].emit('log', 'startup'); assert.deepEqual(logs, ['active']);
  calls[0].emit('provider-ready', { url: 'http://127.0.0.1:45001' }); assert.ok(await b);
});
test('an exited service restarts on the next request', async t => {
  const { provider, calls } = fixture(); t.after(() => provider.close());
  const first = provider.ensure(); calls[0].emit('provider-ready', { url: 'http://127.0.0.1:45001' }); await first;
  calls[0].end(); await wait(0);
  const second = provider.ensure(); assert.equal(calls.length, 2);
  calls[1].emit('provider-ready', { url: 'http://127.0.0.1:45002' }); assert.equal(await second, 'http://127.0.0.1:45002');
});
test('default startup window accommodates a cold provider module scan', () => {
  const { provider } = fixture();
  assert.ok(provider.startupTimeout >= 80000); provider.close();
});
test('startup timeout releases callers and stops the failed child', async () => {
  const { provider, calls } = fixture({ startupTimeout: 10 });
  const [url] = await Promise.all([provider.ensure(), wait(25)]);
  assert.equal(url, null); assert.ok(calls[0].cancelled); provider.close();
});
test('application close cancels startup and prevents late ready events or restarts', async () => {
  const { provider, calls } = fixture(); const ready = provider.ensure(); provider.close();
  calls[0].emit('provider-ready', { url: 'http://127.0.0.1:45001' });
  assert.equal(await ready, null); assert.equal(await provider.ensure(), null); assert.ok(calls[0].cancelled); assert.equal(calls.length, 1);
});
test('a spawn error degrades to ordinary extraction without leaving waiters', async () => {
  const provider = new YouTubeProvider({ task() { throw new Error('spawn failed'); } });
  assert.equal(await provider.ensure(), null); assert.equal(provider.instance, null); provider.close();
});

test('background warmup is idempotent and an application close prevents direct restarts', async () => {
  const {provider,calls}=fixture();const instance=provider.start();
  assert.equal(provider.start(),instance);assert.equal(calls.length,1);
  const waiting=provider.ensure();provider.close();assert.equal(await waiting,null);
  assert.equal(provider.start(),null);assert.equal(calls.length,1);
});

test('a parse joining background warmup receives bounded startup progress', async t => {
  const {provider,calls}=fixture();t.after(()=>provider.close());provider.start();
  calls[0].emit('log','preparing modules');const logs=[];
  const ready=provider.ensure((type,text)=>logs.push([type,text]));
  calls[0].emit('provider-ready',{url:'http://127.0.0.1:45001'});
  assert.ok(await ready);assert.deepEqual(logs,[['log','preparing modules']]);
});

test('timing diagnostics keep numeric stages without saving tokens or binding values', async t => {
  const timings=[];const {provider,calls}=fixture({onTiming:row=>timings.push(row)});t.after(()=>provider.close());
  provider.start();calls[0].emit('provider-timing',{stage:'token',seconds:2,status:200,cookie:'secret',poToken:'secret'});
  calls[0].emit('provider-timing',{stage:'token',seconds:NaN,status:200});
  calls[0].emit('provider-timing',{stage:'private-stage',seconds:1});
  assert.deepEqual(timings,[{stage:'token',seconds:2,status:200}]);
});
