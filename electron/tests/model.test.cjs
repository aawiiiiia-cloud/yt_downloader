'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { navigationUrl, siteForUrl, contentKey, cookieText, readCookies, hasAuth, DownloadQueue } = require('../model.cjs');
const tick = () => new Promise(resolve => setImmediate(resolve));
test('navigation rejects local files, executable schemes, and credentials', () => {
  for (const url of ['file:///C:/private.txt', 'javascript:alert(1)', 'data:text/html,a', 'https://name:password@youtube.com/'])
    assert.throws(() => navigationUrl(url));
  assert.equal(navigationUrl('www.bilibili.com'), 'https://www.bilibili.com/');
});
test('site routing cannot be spoofed by a hostname suffix', () => {
  assert.equal(siteForUrl('https://youtube.com.evil.invalid/watch?v=abc'), null);
  assert.equal(siteForUrl('https://m.bilibili.com/video/BV123'), 'bilibili');
  assert.equal(siteForUrl('https://www.artstation.com/'), null);
});
test('SPA identity tracks the actual video, part or note, not tracking parameters', () => {
  assert.equal(contentKey('https://youtube.com/watch?v=abc&list=RD123'), 'youtube:abc');
  assert.equal(contentKey('https://www.bilibili.com/video/BV123?p=2'), 'bilibili:BV123:2');
  assert.equal(contentKey('https://www.xiaohongshu.com/explore/0123456789abcdef01234567?xsec_token=a'), 'xiaohongshu:0123456789abcdef01234567');
  assert.equal(contentKey('https://www.xiaohongshu.com/explore'), null);
});
test('task cookie snapshots keep HttpOnly flags and exclude unrelated sites', () => {
  const text = cookieText([
    { domain: '.bilibili.com', path: '/', name: 'SESSDATA', value: 'secret', secure: true, httpOnly: true },
    { domain: '.unrelated.invalid', name: 'password', value: 'do-not-export' },
  ], 'bilibili');
  assert.match(text, /#HttpOnly_\.bilibili.com/);
  assert.doesNotMatch(text, /do-not-export/);
  const cookies = readCookies(text, 'bilibili');
  assert.equal(cookies[0].httpOnly, true); assert.equal(cookies[0].secure, true);
  assert.equal(cookies[0].value, 'secret'); assert.equal(hasAuth(cookies, 'bilibili'), true);
  assert.equal(hasAuth([{ domain: '.google.com', name: 'SID', value: 'x' }], 'youtube'), false);
});
test('expired cookies cannot become a new logged-in session', () => {
  assert.deepEqual(readCookies('.bilibili.com\tTRUE\t/\tTRUE\t1\tSESSDATA\tx', 'bilibili'), []);
});
test('download queue runs sequentially and cancels queued work without starting it', async () => {
  const started = [], pending = [];
  const queue = new DownloadQueue(spec => {
    started.push(spec.url);
    let resolve; const promise = new Promise(done => { resolve = done; }); pending.push(resolve);
    return { promise, cancel() {} };
  }, () => {});
  queue.add({ url: 'first', private: 'not-for-renderer' }, 'A');
  const second = queue.add({ url: 'second' }, 'B');
  queue.cancel(second); assert.deepEqual(started, ['first']);
  assert.equal(JSON.stringify(queue.list()).includes('not-for-renderer'), false);
  pending[0]({ count: 1 }); await tick();
  assert.equal(queue.tasks[1].status, 'completed'); assert.equal(queue.tasks[0].status, 'cancelled');
  assert.deepEqual(started, ['first']);
});
test('cancelling an active task does not report completion', async () => {
  let resolve, cancelled = false;
  const queue = new DownloadQueue(() => ({ promise: new Promise(done => { resolve = done; }), cancel() { cancelled = true; } }), () => {});
  const id = queue.add({}, 'A'); queue.cancel(id); resolve({}); await tick();
  assert.equal(cancelled, true); assert.equal(queue.tasks[0].status, 'cancelled');
});
test('failed tasks can retry with their original content snapshot', async () => {
  let attempts = 0;
  const queue = new DownloadQueue(spec => ({ promise: ++attempts === 1 ? Promise.reject(new Error('network')) : Promise.resolve({ url: spec.url }), cancel() {} }), () => {});
  const id = queue.add({ url: 'original' }, 'A'); await tick(); queue.retry(id); await tick();
  assert.equal(queue.tasks[0].status, 'completed'); assert.equal(queue.tasks[0].result.url, 'original');
});
