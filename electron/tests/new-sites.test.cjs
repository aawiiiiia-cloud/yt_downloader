'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const { siteForUrl, contentKey, hasAuth, cookieText, readCookies } = require('../model.cjs');
const { SiteTabs } = require('../site-tabs.cjs');
const { assetUrl, Assets } = require('../assets.cjs');

test('Vimeo and Douyin route by exact domains, including player and share links', () => {
  for (const url of ['https://vimeo.com/123', 'https://player.vimeo.com/video/123']) assert.equal(siteForUrl(url), 'vimeo');
  for (const url of ['https://www.douyin.com/video/123', 'https://v.douyin.com/abc/', 'https://www.iesdouyin.com/share/video/123']) assert.equal(siteForUrl(url), 'douyin');
  for (const url of ['https://douyin.com.evil.invalid/video/123', 'https://fakevimeo.com/123']) assert.equal(siteForUrl(url), null);
});
test('new site content identity handles modal routes and preserves unlisted access hashes', () => {
  assert.equal(contentKey('https://vimeo.com/123'), contentKey('https://player.vimeo.com/video/123?autoplay=0'));
  assert.equal(contentKey('https://vimeo.com/123/abc123'), contentKey('https://player.vimeo.com/video/123?h=abc123'));
  assert.notEqual(contentKey('https://vimeo.com/123'), contentKey('https://vimeo.com/123/abc123'));
  assert.equal(contentKey('https://vimeo.com/channels/staffpicks/123'), 'vimeo:123:');
  assert.equal(contentKey('https://www.douyin.com/?modal_id=123&from=feed'), 'douyin:123');
  assert.equal(contentKey('https://www.iesdouyin.com/share/video/123/'), 'douyin:123');
  assert.equal(contentKey('https://www.douyin.com/note/123'), null);
  assert.equal(contentKey('https://vimeo.com/showcase/123'), null);
});
test('anonymous Douyin cookies are not login proof and credentials stay isolated', () => {
  assert.equal(hasAuth([{domain:'.douyin.com',name:'ttwid',value:'anonymous'}], 'douyin'), false);
  const cookies = [
    {domain:'.douyin.com',name:'sessionid',value:'controlled',httpOnly:true,secure:true},
    {domain:'.vimeo.com',name:'vimeo',value:'controlled-vimeo',secure:true},
    {domain:'.douyin.com.evil.invalid',name:'sessionid',value:'other'},
  ];
  assert.equal(hasAuth(cookies, 'douyin'), true); assert.equal(hasAuth(cookies, 'vimeo'), true);
  const rows = readCookies(cookieText(cookies,'douyin'),'douyin');
  assert.equal(rows.length,1); assert.equal(rows[0].httpOnly,true); assert.equal(rows[0].name,'sessionid');
  assert.equal(readCookies(cookieText(cookies,'douyin'),'vimeo').length,0);
});
test('new site tabs restore their group and reuse the same video across URL variants', () => {
  const tabs=new SiteTabs(); const video=tabs.add('vimeo','https://vimeo.com/123');tabs.select(video.id);
  tabs.switchGroup('douyin');
  const restored=new SiteTabs(tabs.serialize());
  assert.equal(restored.active.group,'douyin');assert.equal(restored.switchGroup('vimeo').id,video.id);
  assert.equal(restored.find('https://player.vimeo.com/video/123').id,video.id);
});
test('new cover CDNs are scoped to their site and receive correct referers', async () => {
  const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAE0lEQVR4nGO8/Po+AwwwwVnoHABhQQKjPQkWWQAAAABJRU5ErkJggg==','base64');
  for(const [site,url,referer] of [
    ['vimeo','https://i.vimeocdn.com/video/123.jpg','https://vimeo.com/'],
    ['douyin','https://p3-pc-sign.douyinpic.com/cover.jpg','https://www.douyin.com/'],
  ]) {
    const assets=new Assets(async (_url,options)=>{assert.equal(options.headers.Referer,referer);return new Response(png)});
    assert.match((await assets.preview(url,site)).dataUrl,/^data:image\/png/);
    assert.throws(()=>assetUrl(url,site==='vimeo'?'douyin':'vimeo'));
  }
  assert.throws(()=>assetUrl('https://douyinpic.com.evil.invalid/a','douyin'));
});
