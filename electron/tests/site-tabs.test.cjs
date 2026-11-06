'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { SiteTabs, MAX_TABS, linkScript, nativeInstagramNavigation, instagramSearchScript } = require('../site-tabs.cjs');

function linkPage(source, options = {}) {
  const handlers = {}, opened = [], location = { href: source };
  const navigation = { currentEntry: { key: 'search-entry' }, addEventListener(name, callback) { handlers[name] = callback; } };
  const context = vm.createContext({ URL, location,
    window: { innerWidth: 1440, innerHeight: 900,
      navigation, addEventListener(name, callback) { handlers[name] = callback; }, open(url) { opened.push(url); } },
    document: { addEventListener() {}, querySelectorAll: () => options.inputs || [] },
    getComputedStyle: input => ({ visibility: 'visible', display: 'block', opacity: '1', ...input.style }) });
  vm.runInContext(linkScript, context);
  return {
    click(target, modifiers = {}, clickOptions = options) {
      const anchor = { tagName: 'A', href: target, target: clickOptions.target || '',
        hasAttribute: name => name === 'download' && !!clickOptions.download };
      const event = { button: 0, ...modifiers, prevented: false, stopped: false,
        composedPath: () => [anchor], preventDefault() { this.prevented = true; },
        stopImmediatePropagation() { this.stopped = true; } };
      handlers.click(event); return { event, opened: [...opened], anchor };
    },
    navigate(url, key) { location.href = url; navigation.currentEntry.key = key; handlers.currententrychange?.(); },
    escape() { handlers.keydown?.({ key: 'Escape' }); },
    searchOpen() { return vm.runInContext(instagramSearchScript, context); },
  };
}
function detailClick(source, target, modifiers = {}, options = {}) {
  return linkPage(source, options).click(target, modifiers);
}

test('Instagram search results and related posts retain their native router in the current view', () => {
  const target = 'https://www.instagram.com/fixture/p/DfFPRM6n0u9/';
  for (const source of [
    'https://www.instagram.com/explore/search/keyword/?q=fixture',
    'https://www.instagram.com/explore/search/',
    'https://www.instagram.com/p/Deg1QJmknzi/',
    'https://www.instagram.com/fixture/reel/Deg1QJmknzi/',
  ]) {
    const { event, opened, anchor } = detailClick(source, target, {}, { target: '_blank' });
    assert.equal(event.prevented, false); assert.equal(event.stopped, false);
    assert.deepEqual(opened, []); assert.equal(anchor.target, '_self');
    assert.equal(nativeInstagramNavigation(source, target), true);
  }
});

test('Instagram sidebar search is recognized only while its search input is visible', () => {
  const target = 'https://www.instagram.com/p/Deg1QJmknzi/';
  const input = { type: 'text', closest: () => null, getAttribute: name => name === 'placeholder' ? 'Search' : null,
    getBoundingClientRect: () => ({ width: 280, height: 36, left: 60, top: 60, right: 340, bottom: 96 }) };
  const visible = detailClick('https://www.instagram.com/', target, {}, { inputs: [input], target: '_blank' });
  assert.equal(visible.event.stopped, false); assert.equal(visible.anchor.target, '_self');
  for (const hidden of [
    { ...input, style: { visibility: 'hidden' } },
    { ...input, style: { opacity: '0' } },
    { ...input, getBoundingClientRect: () => ({ width: 0, height: 0 }) },
    { ...input, getBoundingClientRect: () => ({ width: 280, height: 36, left: -400, top: 60, right: -120, bottom: 96 }) },
    { ...input, getAttribute: () => 'Email' },
  ]) {
    assert.deepEqual(detailClick('https://www.instagram.com/', target, {}, { inputs: [hidden] }).opened, [target]);
  }
});

function searchInput() {
  return { type: 'search', closest: () => null, getAttribute: () => 'Search',
    getBoundingClientRect: () => ({ width: 280, height: 36, left: 60, top: 60, right: 340, bottom: 96 }) };
}
test('Instagram search context survives hidden inputs and repeated modal parse/download/return cycles', () => {
  const root = 'https://www.instagram.com/', first = root + 'p/Deg1QJmknzi/', second = root + 'p/DfFPRM6n0u9/';
  const input = searchInput(), page = linkPage(root, { inputs: [input], target: '_blank' });
  assert.equal(page.click(first).event.stopped, false);
  page.navigate(first, 'post-entry');
  input.style = { display: 'none' };
  assert.equal(page.click(second).event.stopped, false);
  page.navigate(second, 'related-entry');
  page.navigate(root, 'search-entry');
  assert.equal(page.searchOpen(), true);
  for (const url of [second, first, second]) {
    const { event, opened, anchor } = page.click(url);
    assert.equal(event.prevented, false); assert.deepEqual(opened, []); assert.equal(anchor.target, '_self');
    page.navigate(url, 'post-entry'); page.navigate(root, 'search-entry');
  }
});
test('Instagram remembered search is scoped to its history entry and cleared on leaving search', () => {
  const root = 'https://www.instagram.com/', target = root + 'p/Deg1QJmknzi/';
  for (const destination of [root + 'fixture/', root]) {
    const input = searchInput(), page = linkPage(root, { inputs: [input] });
    page.click(target); page.navigate(target, 'post-entry'); input.style = { display: 'none' };
    page.navigate(destination, 'new-entry');
    assert.equal(page.searchOpen(), false); assert.deepEqual(page.click(target).opened, [target]);
    page.navigate(root, 'search-entry');
    assert.equal(page.searchOpen(), false);
  }
});
test('Instagram explicit home links and search dismissal do not retain remembered search', () => {
  const root = 'https://www.instagram.com/', target = root + 'p/Deg1QJmknzi/';
  for (const dismiss of [page => page.click(root), page => page.escape()]) {
    const input = searchInput(), page = linkPage(root, { inputs: [input] });
    page.click(target); page.navigate(target, 'post-entry'); input.style = { visibility: 'hidden' };
    page.navigate(root, 'search-entry'); assert.equal(page.searchOpen(), true);
    dismiss(page);
    assert.equal(page.searchOpen(), false); assert.deepEqual(page.click(target).opened, [target]);
  }
});

test('Instagram home, profiles, explicit new tabs and cross-site links preserve their opening behavior', () => {
  const target = 'https://www.instagram.com/p/Deg1QJmknzi/';
  for (const source of ['https://www.instagram.com/', 'https://www.instagram.com/fixture/']) {
    assert.deepEqual(detailClick(source, target).opened, [target]);
    assert.equal(nativeInstagramNavigation(source, target), false);
  }
  for (const modifiers of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }]) {
    const { event, anchor } = detailClick('https://www.instagram.com/explore/search/', target, modifiers, { target: '_blank' });
    assert.equal(event.stopped, false); assert.equal(anchor.target, '_blank');
  }
  const otherSite = 'https://www.youtube.com/watch?v=fixture';
  assert.deepEqual(detailClick('https://www.instagram.com/explore/search/', otherSite).opened, [otherSite]);
  assert.equal(nativeInstagramNavigation('https://www.instagram.com/p/Deg1QJmknzi/', otherSite, true), false);
  for (const source of ['https://instagram.com.evil.test/explore/search/', 'https://user@www.instagram.com/explore/search/',
    'file:///explore/search/', 'https://www.instagram.com:8123/explore/search/']) {
    assert.equal(nativeInstagramNavigation(source, target, true), false);
  }
});

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
