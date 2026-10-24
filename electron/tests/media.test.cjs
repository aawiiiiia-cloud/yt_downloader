'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { Assets, assetUrl } = require('../assets.cjs');
const { duration, size, videoSize } = require('../ui/media-info.js');
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAE0lEQVR4nGO8/Po+AwwwwVnoHABhQQKjPQkWWQAAAABJRU5ErkJggg==', 'base64');
test('durations preserve seconds and support hours; unknown sizes are not zero', () => {
  assert.equal(duration(3527), '58:47'); assert.equal(duration(7127), '1:58:47');
  assert.equal(duration(0), '00:00'); assert.equal(duration(null), '时长未知');
  assert.equal(size(340000), '0.34 MB'); assert.equal(size(340000, true), '约 0.34 MB'); assert.equal(size(null), '大小未知');
});
test('selected video size includes audio, estimates bitrate, and avoids double counting muxed streams', () => {
  const item = { duration: 100, formats: [
    { id: 'mux', video: true, audio: true, size: 2000000 },
    { id: 'audio', video: false, audio: true, size: 1000000 },
    { id: 'hd', video: true, audio: false, size: 10000000 },
    { id: 'estimate', video: true, audio: false, bitrate: 800 },
  ] };
  assert.equal(videoSize(item, 'hd').size, 11000000);
  assert.equal(videoSize(item, 'mux').size, 2000000);
  assert.deepEqual(videoSize(item, 'estimate'), { size: 11000000, estimated: true });
  assert.deepEqual(videoSize(item, '', true), { size: 2400000, estimated: true });
  assert.equal(videoSize({ formats: [{ id: 'x', video: true }] }, 'x').size, null);
});
test('trusted HTTP covers are upgraded; local, credentialed and spoofed resources are rejected', () => {
  assert.equal(assetUrl('http://i0.hdslb.com/cover.jpg', 'bilibili'), 'https://i0.hdslb.com/cover.jpg');
  assert.equal(assetUrl('//i.ytimg.com/cover.jpg', 'youtube'), 'https://i.ytimg.com/cover.jpg');
  for (const value of ['http://127.0.0.1/private', 'https://hdslb.com.evil.invalid/a', 'https://u:p@i0.hdslb.com/a']) assert.throws(() => assetUrl(value, 'bilibili'));
});
test('cover requests use the site referer and cache bytes; HTML cannot be shown as an image', async () => {
  let calls = 0;
  const assets = new Assets(async (url, options) => {
    calls++; assert.equal(url, 'https://i0.hdslb.com/cover.jpg'); assert.equal(options.headers.Referer, 'https://www.bilibili.com/');
    return new Response(png);
  });
  const preview = await assets.preview('http://i0.hdslb.com/cover.jpg', 'bilibili');
  assert.match(preview.dataUrl, /^data:image\/png;base64,/);
  await assets.preview('http://i0.hdslb.com/cover.jpg', 'bilibili'); assert.equal(calls, 1);
  const invalid = new Assets(async () => new Response('<html>blocked</html>', { headers: { 'content-type': 'image/jpeg' } }));
  await assert.rejects(() => invalid.preview('https://i0.hdslb.com/a', 'bilibili'), /没有返回/);
});
test('range metadata uses full resource size and cancels the response without downloading it', async () => {
  const methods = [];
  const assets = new Assets(async (_url, options) => {
    methods.push(options.method);
    if (options.method === 'HEAD') return new Response(null, { status: 405 });
    assert.equal(options.headers.Range, 'bytes=0-0');
    return new Response(png, { status: 206, headers: { 'content-range': 'bytes 0-0/340000', 'content-length': '1', 'content-type': 'image/jpeg' } });
  });
  assert.equal((await assets.sourceSize(['https://sns-na-i11.xhscdn.com/a'], 'xiaohongshu')).size, 340000);
  assert.deepEqual(methods, ['HEAD', 'GET']);
});
test('redirects cannot turn cover or metadata fetching into local network requests', async () => {
  let requests = 0;
  const assets = new Assets(async () => { requests++; return new Response(null, { status: 302, headers: { location: 'http://127.0.0.1/private' } }); });
  await assert.rejects(() => assets.preview('https://i0.hdslb.com/a', 'bilibili'), /受支持/);
  assert.equal(requests, 1);
});
