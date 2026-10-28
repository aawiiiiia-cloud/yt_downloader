'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { SiteTabs, MAX_TABS, linkScript } = require('../site-tabs.cjs');

function detailClick(source, target, modifiers = {}) {
  let handler; const opened = [];
  const event = { button: 0, ...modifiers, prevented: false, stopped: false,
    composedPath: () => [{ tagName: 'A', href: target, hasAttribute: () => false }],
    preventDefault() { this.prevented = true; }, stopImmediatePropagation() { this.stopped = true; } };
  vm.runInNewContext(linkScript, { URL, location: { href: source },
    window: { addEventListener(name, callback) { if (name === 'click') handler = callback; }, open(url) { opened.push(url); } },
    document: { addEventListener() {} } });
  handler(event); return { event, opened };
}

test('Xiaohongshu feed, search and profile clicks reach the native note overlay router', () => {
  const target = 'https://www.xiaohongshu.com/explore/0123456789abcdef01234567?xsec_source=pc_feed';
  for (const source of ['https://www.xiaohongshu.com/explore', 'https://www.xiaohongshu.com/search_result?keyword=test',
    'https://www.xiaohongshu.com/user/profile/1123456789abcdef01234567']) {
    const { event, opened } = detailClick(source, target);
    assert.equal(event.prevented, false); assert.equal(event.stopped, false); assert.deepEqual(opened, []);
  }
});

test('Xiaohongshu related-note clicks can replace the current note overlay', () => {
  const { event, opened } = detailClick('https://www.xiaohongshu.com/explore/1123456789abcdef01234567',
    'https://www.xiaohongshu.com/explore/0123456789abcdef01234567');
  assert.equal(event.stopped, false); assert.deepEqual(opened, []);
});

test('YouTube detail links and cross-site links still open in their own group', () => {
  for (const [source, target] of [
    ['https://www.youtube.com/', 'https://www.youtube.com/watch?v=fixture'],
    ['https://www.xiaohongshu.com/explore', 'https://www.youtube.com/watch?v=fixture'],
    ['https://www.youtube.com/', 'https://www.xiaohongshu.com/explore/0123456789abcdef01234567'],
  ]) {
    const { event, opened } = detailClick(source, target);
    assert.equal(event.prevented, true); assert.equal(event.stopped, true); assert.deepEqual(opened, [target]);
  }
});

test('modified clicks and Bilibili part changes retain their existing browser behavior', () => {
  for (const modifiers of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }]) {
    assert.equal(detailClick('https://www.youtube.com/', 'https://www.youtube.com/watch?v=fixture', modifiers).event.prevented, false);
  }
  const { event, opened } = detailClick('https://www.bilibili.com/video/BVfixture?p=1', 'https://www.bilibili.com/video/BVfixture?p=2');
  assert.equal(event.prevented, false); assert.deepEqual(opened, []);
});

test('site groups remember their own last active tab', () => {
  const tabs = new SiteTabs(); const home = tabs.active;
  const video = tabs.add('youtube', 'https://www.youtube.com/watch?v=A'); tabs.select(video.id);
  const bili = tabs.switchGroup('bilibili');
  assert.equal(tabs.switchGroup('youtube'), video); assert.equal(tabs.switchGroup('bilibili'), bili);
  assert.equal(tabs.rows.length, 3); assert.ok(tabs.rows.includes(home));
});
test('content deduplication ignores tracking while retaining separate Bilibili parts', () => {
  const tabs = new SiteTabs();
  const video = tabs.add('bilibili', 'https://www.bilibili.com/video/BVfixture/?p=2&tracking=abc');
  assert.equal(tabs.find('https://www.bilibili.com/video/BVfixture/?p=2'), video);
  assert.equal(tabs.find('https://www.bilibili.com/video/BVfixture/?p=1'), undefined);
  assert.equal(tabs.find('https://www.bilibili.com/video/BVfixture/?p=2', 'youtube'), undefined);
});
test('closing tabs selects a neighbor and closing the last creates a usable home', () => {
  const tabs = new SiteTabs(); const home = tabs.active;
  const video = tabs.add('youtube', 'https://www.youtube.com/watch?v=A'); tabs.select(video.id);
  tabs.remove(home.id); assert.equal(tabs.active, video);
  tabs.remove(video.id); assert.equal(tabs.active.group, 'youtube'); assert.equal(tabs.active.url, 'https://www.youtube.com/');
  assert.throws(() => tabs.select(video.id), /已关闭/);
});
test('restart saves only safe navigation metadata and restores group selection', () => {
  const tabs = new SiteTabs(); const video = tabs.add('youtube', 'https://www.youtube.com/watch?v=A');
  video.view = { private: 'view' }; video.state = { result: 'signed resource URL', cookieFile: 'secret' };
  tabs.select(video.id); tabs.switchGroup('xiaohongshu');
  const json = JSON.stringify(tabs.serialize());
  assert.ok(!json.includes('cookieFile') && !json.includes('signed resource') && !json.includes('private'));
  const restored = new SiteTabs(JSON.parse(json)); assert.equal(restored.active.group, 'xiaohongshu');
  assert.equal(restored.switchGroup('youtube').url, video.url);
  const malformed = new SiteTabs({ tabs: [{group:'youtube',url:'file:///C:/secret'}, {group:'invalid',url:'https://example.com'}] });
  assert.equal(malformed.rows.length, 1); assert.equal(malformed.active.url, '');
});
test('tab limit bounds resource use without preventing closing the last tab', () => {
  const tabs = new SiteTabs(); while (tabs.rows.length < MAX_TABS) tabs.add('youtube');
  assert.throws(() => tabs.add('youtube'), /36/);
  const biliId = tabs.rows[0].id; tabs.rows[0].group = 'bilibili'; tabs.select(biliId);
  tabs.remove(biliId); assert.equal(tabs.rows.length, MAX_TABS); assert.equal(tabs.active.group, 'bilibili');
  new Function(linkScript);
});

test('restart discards old Taobao login relay tabs without discarding product tabs', () => {
  const relay = 'https://detail.tmall.com/wow/home/_____tmd_____/page/login_jump?rand=expired-secret';
  const saved = {tabs:[{id:'relay',group:'taobao',url:relay},{id:'product',group:'taobao',url:'https://detail.tmall.com/item.htm?id=123'}],activeId:'relay'};
  const restored = new SiteTabs(saved);
  assert.equal(restored.rows.length,1); assert.equal(restored.active.id,'product');
  assert.doesNotMatch(JSON.stringify(restored.serialize()),/expired-secret|login_jump/);
  const tabs = new SiteTabs(); const product = tabs.add('taobao','https://item.taobao.com/item.htm?id=456');
  tabs.select(tabs.add('taobao',relay).id);
  const metadata = tabs.serialize();
  assert.equal(metadata.activeId,product.id); assert.equal(metadata.lastActive.taobao,product.id);
  assert.doesNotMatch(JSON.stringify(metadata),/expired-secret|login_jump/);
  assert.equal(new SiteTabs(metadata).active.id,product.id);
});
