'use strict';
const test = require('node:test'), assert = require('node:assert/strict'), vm = require('node:vm');
const { contentKey, siteForUrl, hasAuth, cookieText } = require('../model.cjs');
const { assetUrl } = require('../assets.cjs');
const { workerProxy } = require('../proxy-route.cjs');
const { postSnapshot, snapshotScript, completeContext } = require('../instagram-page.cjs');
const { loginPlan, loginHost } = require('../web-login.cjs');
const fixture = require('../fixtures/instagram.json');
const url = 'https://www.instagram.com/p/Deg1QJmknzi/';
const doc = data => ({ querySelectorAll: () => [{ textContent: '{broken' }, { textContent: JSON.stringify(data) }] });

test('Instagram identities canonicalize posts, reels and carousel index tracking', () => {
  for (const path of ['p/Deg1QJmknzi/?img_index=4', 'reel/Deg1QJmknzi/', 'reels/Deg1QJmknzi/',
    'heylang_ciel/p/Deg1QJmknzi/?img_index=4', 'user.name/reel/Deg1QJmknzi/', 'fixture/reels/Deg1QJmknzi/'])
    assert.equal(contentKey('https://www.instagram.com/' + path), 'instagram:Deg1QJmknzi');
  assert.equal(siteForUrl(url), 'instagram');
  for (const target of ['https://instagram.com.evil.invalid/p/Deg1QJmknzi/', 'https://www.instagram.com/artist/',
    'https://user:pass@www.instagram.com/p/Deg1QJmknzi/', 'https://www.instagram.com:9999/p/Deg1QJmknzi/',
    'https://www.instagram.com/accounts/p/Deg1QJmknzi/', 'https://www.instagram.com/share/p/Deg1QJmknzi/',
    'https://www.instagram.com/stories/p/Deg1QJmknzi/'])
    assert.equal(contentKey(target), null);
});
test('author-prefixed posts reuse their own data and never parse profile recommendations', () => {
  const nested = url.replace('/p/', '/fixture/p/');
  assert.deepEqual(postSnapshot(doc(fixture), nested), postSnapshot(doc(fixture), url));
  const profile = 'https://www.instagram.com/fixture/';
  assert.equal(postSnapshot(doc(fixture), profile), null);
  assert.equal(postSnapshot(doc(fixture), profile + 'p/DfFPRM6n0u9/'), null);
  assert.equal(postSnapshot(doc(fixture), 'https://user:pass@www.instagram.com/fixture/p/Deg1QJmknzi/'), null);
});
test('snapshots prefer metadata-rich duplicate video data and retain only scalar sizes', () => {
  const rich = structuredClone(fixture), lean = structuredClone(fixture);
  rich.carousel_media[1].video_dash_manifest = '<MPD mediaPresentationDuration="PT37.166668S"/>';
  rich.carousel_media[1].video_versions[0].file_size = 1250000;
  rich.carousel_media[1].video_versions[1].content_length = {password: 'secret'};
  lean.carousel_media[1].video_duration = null;
  for (const rows of [[rich, lean], [lean, rich]]) {
    const result = postSnapshot(doc(rows), url);
    const video = result.instagram.post.carousel_media[1];
    assert.ok(video.video_dash_manifest.includes('PT37.166668S'));
    assert.equal(video.video_versions.find(row => row.width === 720).file_size, 1250000);
    assert.doesNotMatch(JSON.stringify(result), /password|secret/);
  }
});
test('page snapshots prune secrets and match both shortcode and numeric identity', () => {
  const result = postSnapshot(doc({ unrelated: { password: 'secret' }, data: fixture }), url);
  assert.equal(result.instagram.post.carousel_media.length, 3);
  assert.ok(completeContext(result));
  assert.doesNotMatch(JSON.stringify(result), /private_token|password|secret|excluded/);
  assert.equal(postSnapshot(doc({ ...fixture, pk: '999' }), url), null);
  assert.equal(postSnapshot(doc(fixture), url.replace('Deg1', 'Dfg1')), null);
  const fromScript = vm.runInNewContext(snapshotScript, { document: doc(fixture), location: { href: url }, URL });
  assert.equal(fromScript.instagram.shortcode, fixture.code);
  const incomplete = structuredClone(result); incomplete.instagram.post.carousel_media_count = 4;
  assert.equal(completeContext(incomplete), false);
});
test('Instagram CDN signed queries remain unchanged and untrusted hosts are refused', () => {
  const signed = 'https://scontent.cdninstagram.com/a.jpg?sig=aa%2Bb%2Fc&oe=12';
  assert.equal(assetUrl(signed, 'instagram'), signed);
  assert.throws(() => assetUrl('https://cdninstagram.com.evil.invalid/a.jpg','instagram'));
  assert.throws(() => assetUrl('https://127.0.0.1/a.jpg','instagram'));
});
test('snapshots prefer a complete duplicate and retain large late image candidates', () => {
  const incomplete = structuredClone(fixture);
  incomplete.carousel_media[1].video_versions = [];
  for (const data of [[fixture, incomplete], [incomplete, fixture]]) {
    assert.ok(completeContext(postSnapshot(doc(data), url)));
  }
  const large = structuredClone(fixture);
  large.carousel_media[0].image_versions2.candidates = Array.from({ length: 40 }, (_, i) => ({
    url: `https://fixture.cdninstagram.com/${i}.jpg`, width: i + 1, height: i + 1,
  }));
  const result = postSnapshot(doc({ items: [...Array(501).fill(null), large] }), url);
  assert.equal(result.instagram.post.carousel_media[0].image_versions2.candidates.length, 32);
  assert.equal(result.instagram.post.carousel_media[0].image_versions2.candidates[0].width, 40);
  const duplicate = structuredClone(result); duplicate.instagram.post.carousel_media[2].pk = '101';
  assert.equal(completeContext(duplicate), false);
  result.instagram.post.carousel_media_count = 0;
  assert.equal(completeContext(result), false);
});
test('Instagram login uses its official page and session cookies only', () => {
  assert.equal(loginPlan('instagram').direct, true);
  assert.ok(loginHost('instagram', loginPlan('instagram').url));
  assert.ok(!loginHost('instagram','https://www.instagram.com/p/Deg1QJmknzi/'));
  assert.ok(!hasAuth([{domain:'.instagram.com',name:'csrftoken',value:'anonymous'}],'instagram'));
  const cookies = [{domain:'.instagram.com',name:'sessionid',value:'test'}, {domain:'.youtube.com',name:'SID',value:'private'}];
  assert.ok(hasAuth(cookies, 'instagram'));
  assert.doesNotMatch(cookieText(cookies,'instagram'), /private|youtube/);
});
test('PAC SOCKS routes preserve remote DNS and never silently downgrade to direct', () => {
  assert.equal(workerProxy('SOCKS5 127.0.0.1:13659; SOCKS 127.0.0.1:13659; DIRECT'), 'socks5h://127.0.0.1:13659');
  assert.equal(workerProxy('SOCKS 127.0.0.1:5555; DIRECT'), 'socks5h://127.0.0.1:5555');
  assert.equal(workerProxy('SOCKS4 127.0.0.1:5555'), 'socks4a://127.0.0.1:5555');
  assert.equal(workerProxy('PROXY localhost:7890; DIRECT'), 'http://localhost:7890');
  assert.equal(workerProxy('PROXY localhost:80'), 'http://localhost:80');
  assert.equal(workerProxy('HTTPS localhost:443'), 'https://localhost:443');
  assert.equal(workerProxy('SOCKS5 [::1]:13659'), 'socks5h://[::1]:13659');
  assert.equal(workerProxy('DIRECT'), '');
  assert.equal(workerProxy('', 'socks5://localhost:7890'), 'socks5://localhost:7890');
  for (const route of ['', 'UNKNOWN localhost:7890', 'PROXY user:pass@localhost:7890', 'PROXY localhost:7890/path',
    'PROXY localhost:0', 'SOCKS5 localhost:65536'])
    assert.throws(() => workerProxy(route));
});
