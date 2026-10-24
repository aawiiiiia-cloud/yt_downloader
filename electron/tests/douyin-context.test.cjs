'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const { resolveDouyinContext } = require('../douyin-context.cjs');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const context = { videoId: '123', detail: { aweme_id: '123' } };
const base = { url: 'https://www.douyin.com/video/123', cached: () => null, read: async () => null,
  supplement: async () => null, graceMs: 5, pageTimeoutMs: 100, supplementTimeoutMs: 100, pollMs: 5 };
test('a matching cached video returns immediately without page reads or supplement requests', async () => {
  const result = await resolveDouyinContext({ ...base, cached: () => context,
    read() { throw new Error('unnecessary read'); }, supplement() { throw new Error('unnecessary request'); } });
  assert.equal(result, context);
});
test('data available on the visible page avoids even starting a background page', async () => {
  let probes = 0;
  assert.equal(await resolveDouyinContext({ ...base, read: async () => context, supplement: async () => { probes++; } }), context);
  await wait(15); assert.equal(probes, 0);
});
test('missing page data starts the supplement before the page wait ends and aborts the slower reader', async () => {
  let reading, launched = false;
  const result = await resolveDouyinContext({ ...base,
    read: signal => { reading = signal; return new Promise(() => {}); },
    supplement: async () => { assert.ok(!reading.aborted); launched = true; return context; } });
  assert.equal(result, context); assert.ok(launched); assert.ok(reading.aborted);
});
test('later visible-page data wins and closes an already-started background request', async () => {
  let release, probeSignal;
  const result = resolveDouyinContext({ ...base, read: () => new Promise(resolve => { release = resolve; }),
    supplement: signal => { probeSignal = signal; release(context); return new Promise(() => {}); } });
  assert.equal(await result, context); assert.ok(probeSignal.aborted);
});
test('navigation abort returns promptly and cannot publish a late result for the old video', async () => {
  const controller = new AbortController(); let release, probeSignal;
  const result = resolveDouyinContext({ ...base, signal: controller.signal,
    supplement: signal => { probeSignal = signal; return new Promise(resolve => { release = resolve; }); } });
  await wait(15); controller.abort(); assert.equal(await result, null); assert.ok(probeSignal.aborted);
  release(context); await wait(0);
});
test('both missing sources are bounded even when a page operation never settles', async () => {
  let pageSignal, probeSignal;
  const result = await resolveDouyinContext({ ...base, pageTimeoutMs: 15, supplementTimeoutMs: 20,
    read: signal => { pageSignal = signal; return new Promise(() => {}); },
    supplement: signal => { probeSignal = signal; return new Promise(() => {}); } });
  assert.equal(result, null); assert.ok(pageSignal.aborted); assert.ok(probeSignal.aborted);
});
test('another video from a feed cannot win the race for the current video', async () => {
  const other = { videoId: '456', detail: { aweme_id: '456' } };
  assert.equal(await resolveDouyinContext({ ...base, cached: () => other, read: async () => other, supplement: async () => context }), context);
});
