'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { workSnapshot } = require('../zcool-page.cjs');
const { contentKey, siteForUrl, cookieText, hasAuth, readCookies, downloadTargetKey } = require('../model.cjs');
const { LoginState } = require('../login-state.cjs');
const { fingerprint } = require('../youtube-auth.cjs');
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

test('ZCOOL native video snapshots retain public media fields and isolate third-party URLs and credentials', () => {
  const root=fixture(),data=root.props.pageProps.data;
  const url='https://video.zcool.cn/alivod/sample.mp4?k=keep&t=keep';
  data.productVideos=[{id:3319907,type:4,url,cover:'https://video.zcool.cn/alivod/sample.mp4!vframeOffset0?k=cover',width:720,height:405,proofToken:'private-proof'},
    {url:'https://player.vimeo.com/video/123'},{url:'https://video.zcool.cn.evil.invalid/sample.mp4'},
    {url:'https://u:p@video.zcool.cn/sample.mp4'},{url:'file:///C:/private.mp4'}];
  const value=workSnapshot(root,'https://www.zcool.com.cn/work/ZNzQyNjY2ODg=.html');
  assert.equal(value.videos.length,1);assert.equal(value.videos[0].url,url);assert.equal(value.skippedVideos,4);
  assert.doesNotMatch(JSON.stringify(value),/private-proof|secret-account|vimeo|evil|C:\//);
});

test('ZCOOL video-only works and malformed covers do not depend on an image collection', () => {
  const root=fixture(),data=root.props.pageProps.data;
  delete data.productImages;
  data.productVideos=[{id:1,url:'https://video.zcool.cn/alivod/sample.mp4',cover:'invalid'}];
  let value=workSnapshot(root,url);
  assert.equal(value.images.length,0);assert.equal(value.videos.length,1);assert.equal(value.videos[0].cover,undefined);
  data.productVideos=Array.from({length:30},(_,id)=>({id,url:'https://video.zcool.cn/alivod/'+id+'.mp4'}));
  value=workSnapshot(root,url);assert.equal(value.videos.length,24);assert.equal(value.skippedVideos,6);
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
function loginCookies() {
  return ['zcool_mt', 'zcool_logon_new'].map(name => ({ domain: '.zcool.com.cn', path: '/', name,
    value: 'fixture-' + name, secure: true, httpOnly: true, expirationDate: Date.now() / 1000 + 3600 }));
}
test('ZCOOL login requires both official credentials, never one token, a profile or anonymous cookies', () => {
  const cookies = loginCookies();
  assert.equal(hasAuth(cookies, 'zcool'), true);
  assert.equal(hasAuth([cookies[0]], 'zcool'), false);
  assert.equal(hasAuth([cookies[1]], 'zcool'), false);
  assert.equal(hasAuth([{ ...cookies[0], name: 'JSESSIONID' }, { ...cookies[1], name: 'z_ui' }], 'zcool'), false);
  for (const bad of [{ value: '' }, { expirationDate: Date.now() / 1000 - 1 }, { domain: '.zcool.cn' },
    { domain: '.zcool.com.cn.evil.invalid' }, { domain: '.bilibili.com' }])
    assert.equal(hasAuth([cookies[0], { ...cookies[1], ...bad }], 'zcool'), false, JSON.stringify(bad));
  assert.equal(hasAuth(cookies.map(cookie => ({ ...cookie, domain: 'www.zcool.com.cn' })), 'zcool'), true);
  assert.equal(hasAuth([...cookies, { ...cookies[0], expirationDate: 1 }], 'zcool'), true);
});
test('ZCOOL login, logout, credential replacement and rejection retain the exact authenticated session', () => {
  const state = new LoginState(), cookies = loginCookies();
  state.observe([cookies[0]]); assert.equal(state.state().detected.zcool, false);
  state.observe(cookies); const key = state.keys.zcool; assert.ok(key); state.reject('zcool', key);
  const extra = [...cookies, { ...cookies[0], name: 'z_ui', value: 'changed-profile' }];
  state.observe(extra); assert.equal(state.keys.zcool, key); assert.ok(state.state().expired.zcool);
  const restored = new LoginState(JSON.parse(JSON.stringify(state.invalid)));
  restored.observe(extra); assert.ok(restored.state().expired.zcool);
  restored.observe([cookies[0]]); assert.equal(restored.state().detected.zcool, false); assert.equal(restored.state().expired.zcool, false);
  const fresh = [cookies[0], { ...cookies[1], value: 'fresh-login' }]; restored.observe(fresh);
  assert.ok(restored.state().detected.zcool); assert.equal(restored.state().expired.zcool, false);
  assert.equal(restored.reject('zcool', key), false);
  assert.doesNotMatch(JSON.stringify(restored.invalid), /fixture-|fresh-login|changed-profile/);
});
test('imported ZCOOL cookies preserve both credentials and domain isolation without exposing their values', () => {
  const cookies = loginCookies(), text = cookieText([...cookies, { ...cookies[0], domain: '.youtube.com', value: 'foreign-secret' }], 'zcool');
  const imported = readCookies(text, 'zcool');
  assert.equal(imported.length, 2); assert.ok(imported.every(cookie => cookie.httpOnly));
  assert.ok(hasAuth(imported, 'zcool')); assert.equal(fingerprint(imported, 'zcool'), fingerprint(cookies, 'zcool'));
  assert.doesNotMatch(text, /foreign-secret/); assert.equal(fingerprint(imported, 'bilibili'), '');
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
