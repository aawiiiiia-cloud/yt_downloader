'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { workSnapshot } = require('../zcool-page.cjs');
const { contentKey, siteForUrl, cookieText, hasAuth, downloadTargetKey } = require('../model.cjs');
const { SiteTabs } = require('../site-tabs.cjs');
const { Assets, assetUrl } = require('../assets.cjs');
const { classifyFailure } = require('../task-failure.cjs');
const url = 'https://www.zcool.com.cn/work/ZNzQyNjY2ODg=.html';
function fixture() {
  return { query: { id: 'ZNzQyNjY2ODg=.html' }, props: { pageProps: { data: {
    title: '测试作品', creatorObj: { username: '测试作者', token: 'secret-account' },
    account: 'secret-account', productImages: Array.from({ length: 28 }, (_, index) => ({ id: index + 1,
      urlBig: `https://img.zcool.cn/community/${index}.jpg?x-oss-process=image/resize,w_1280&k=keep&t=keep`,
      width: 2968, height: 3957, proofToken: 'secret-proof' })),
    recommendations: [{ url: 'https://img.zcool.cn/unrelated.jpg' }], productVideos: [{ url: 'secret-video' }],
  } } } };
}
test('ZCOOL routing and tab identities retain padded work IDs and reject non-work pages', () => {
  assert.equal(siteForUrl(url), 'zcool'); assert.equal(siteForUrl('https://www.zcool.com.cn.evil.invalid/'), null);
  assert.equal(contentKey(url + '?tracking=1'), 'zcool:ZNzQyNjY2ODg=');
  assert.equal(contentKey(url.replace('=.html', '%3D.html')), contentKey(url));
  assert.equal(contentKey('https://www.zcool.com.cn/u/123'), null);
  assert.equal(contentKey(url.replace('www.', 'name:password@www.')), null);
  const tabs = new SiteTabs(); tabs.select(tabs.add('zcool', url).id);
  assert.equal(new SiteTabs(tabs.serialize()).active.group, 'zcool');
  assert.equal(downloadTargetKey({ url }), downloadTargetKey({ url: url + '?track=other' }));
});
test('ZCOOL snapshot reads 28 body images and cannot leak the full store or label a stale work', () => {
  const root = fixture(), snapshot = workSnapshot(root, url);
  assert.equal(snapshot.images.length, 28); assert.equal(snapshot.author, '测试作者');
  assert.doesNotMatch(JSON.stringify(snapshot), /secret-account|secret-proof|secret-video|unrelated.jpg/);
  assert.equal(snapshot.skippedVideos, 1);
  assert.equal(workSnapshot(root, url.replace('ZNzQyNjY2ODg=', 'ZanotherWork')).mismatch, true);
  root.props.pageProps.data.productImages = Array(401).fill({}); assert.equal(workSnapshot(root, url), null);
});
test('ZCOOL image allowlist preserves signed queries while anonymous cookies never prove login', () => {
  const asset = fixture().props.pageProps.data.productImages[0].urlBig;
  assert.equal(assetUrl(asset, 'zcool'), asset);
  assert.throws(() => assetUrl('https://img.zcool.cn.evil.invalid/a.jpg', 'zcool'));
  const cookies = [{ domain: '.zcool.com.cn', name: 'JSESSIONID', value: 'anonymous' },
    { domain: '.zcool.cn', name: 'fixture-cdn', value: 'cdn-only' },
    { domain: '.youtube.com', name: 'SID', value: 'foreign' }];
  assert.equal(hasAuth(cookies, 'zcool'), false);
  assert.match(cookieText(cookies, 'zcool'), /anonymous/); assert.doesNotMatch(cookieText(cookies, 'zcool'), /foreign/);
  assert.match(cookieText(cookies, 'zcool'), /cdn-only/);
});
test('ZCOOL dimensions are read from the response and use the correct origin and full URL', async () => {
  const bytes = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAE0lEQVR4nGO8/Po+AwwwwVnoHABhQQKjPQkWWQAAAABJRU5ErkJggg==', 'base64');
  const requests = [], asset = fixture().props.pageProps.data.productImages[0].urlBig;
  const images = new Assets(async (value, options) => { requests.push({ value, options });
    return new Response(options.method === 'HEAD' ? null : bytes, { headers: { 'content-type': 'image/png', 'content-length': String(bytes.length) } }); });
  const result = await images.sourceSize([asset], 'zcool', undefined, url, true);
  assert.equal(result.width, 4); assert.equal(result.height, 3);
  assert.ok(requests.every(row => row.value === asset && row.options.headers.Referer === 'https://www.zcool.com.cn/'));
});
test('resolution limitations do not expire ZCOOL credentials or turn into generic failures', () => {
  const result = classifyFailure({ code: 'IMAGE_RESOLUTION_LIMITED', message: '请在左侧登录后重新解析' }, [], { hadCredentials: true });
  assert.equal(result.code, 'IMAGE_RESOLUTION_LIMITED'); assert.match(result.message, /未保存/);
});
