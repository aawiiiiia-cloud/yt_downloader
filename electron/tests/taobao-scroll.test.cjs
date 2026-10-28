"use strict";
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const vm = require('node:vm');
const { HOME_MIN_WIDTH, HOME_SCROLL_ATTRIBUTE, HOME_SCROLL_CSS, isTaobaoHome, TaobaoHomeScroll } = require('../taobao-scroll.cjs');
const HOME = 'https://www.taobao.com/';
const PRODUCT = 'https://item.taobao.com/item.htm?id=123';
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
class FakeWeb extends EventEmitter {
  constructor(url = HOME) {
    super(); this.url = url; this.destroyed = false; this.insertions = []; this.scripts = []; this.attributes = new Set();
    this.mainFrame = { executeJavaScript: async code => {
      this.scripts.push(code);
      if (this.beforeExecute) await this.beforeExecute(code);
      return vm.runInNewContext(code, { URL, location: { href: this.url }, document: { documentElement: {
        toggleAttribute: (name, enabled) => enabled ? this.attributes.add(name) : this.attributes.delete(name),
      } } });
    } };
  }
  isDestroyed() { return this.destroyed; }
  getURL() { return this.url; }
  async insertCSS(css, options) {
    const key = 'style-' + (this.insertions.length + 1);
    this.insertions.push({ css, options, key });
    if (this.beforeInsert) await this.beforeInsert();
    return key;
  }
  // Deliberately no removeInsertedCSS: user-origin sheets are deactivated
  // using their root selector, not an unreliable Electron removal API.
  navigate(url) { this.url = url; this.attributes.clear(); this.emit('did-navigate', {}, url); this.emit('dom-ready'); }
  inPage(url) { this.url = url; this.emit('did-navigate-in-page', {}, url, true); }
  active() { return this.attributes.has(HOME_SCROLL_ATTRIBUTE); }
}

test('Taobao home compatibility uses an exact HTTPS home URL allowlist', () => {
  for (const url of [HOME, 'https://taobao.com/', HOME + '?q=test', HOME + 'index.html', HOME + 'index.htm']) assert.ok(isTaobaoHome(url), url);
  for (const url of [PRODUCT, 'https://detail.tmall.com/', HOME + 'search', HOME + 'login', 'https://login.taobao.com/', 'http://www.taobao.com/', 'https://www.taobao.com:8443/', 'https://user@www.taobao.com/', 'https://www.taobao.com.evil.test/', 'javascript:void(0)', 'invalid']) assert.equal(isTaobaoHome(url), false, url);
});
test('every home CSS rule is attribute-scoped and preserves zoom, height and positioning', () => {
  assert.equal(HOME_MIN_WIDTH, 1200);
  assert.match(HOME_SCROLL_CSS, /overflow-x:\s*auto !important/);
  assert.match(HOME_SCROLL_CSS, /min-width:\s*1200px !important/);
  assert.doesNotMatch(HOME_SCROLL_CSS, /(?:zoom|transform|position|scroll-behavior)\s*:/);
  for (const match of HOME_SCROLL_CSS.matchAll(/([^{}]+)\{([^}]+)\}/g)) if (!match[1].includes("::-webkit-scrollbar")) assert.doesNotMatch(match[2], /(?:min-|max-)?height\s*:/);
  for (const match of HOME_SCROLL_CSS.matchAll(/([^{}]+)\{/g)) assert.ok(match[1].includes('[' + HOME_SCROLL_ATTRIBUTE + ']'), match[1]);
});
test('non-home pages, login documents and other sites are not styled or scripted', async () => {
  for (const url of [PRODUCT, 'https://login.taobao.com/member/login.jhtml', HOME + 'search', 'https://www.bilibili.com/']) {
    const web = new FakeWeb(url), scroll = new TaobaoHomeScroll(web); await scroll.pending;
    assert.equal(web.insertions.length, 0); assert.equal(web.scripts.length, 0); assert.equal(web.active(), false);
  }
});
test('duplicate ready events install one user-origin sheet and activate only the main root', async () => {
  const web = new FakeWeb(), scroll = new TaobaoHomeScroll(web);
  web.emit('dom-ready'); web.emit('did-finish-load'); await scroll.pending;
  assert.equal(web.insertions.length, 1); assert.deepEqual(web.insertions[0].options, { cssOrigin: 'user' });
  assert.equal(web.active(), true); assert.equal(scroll.enabled, true);
  assert.ok(web.scripts.every(code => !/require\(|window\.collector|querySelectorAll|iframe/.test(code)));
});
test('fresh document navigation and reload install fresh home rules', async () => {
  const web = new FakeWeb(), scroll = new TaobaoHomeScroll(web); await scroll.pending;
  const original = scroll.key; web.navigate(HOME); await scroll.pending;
  assert.notEqual(scroll.key, original); assert.equal(web.insertions.length, 2); assert.equal(web.active(), true);
});
test('SPA departure deactivates all home rules and return reuses the inert sheet', async () => {
  const web = new FakeWeb(), scroll = new TaobaoHomeScroll(web); await scroll.pending;
  const key = scroll.key; web.inPage(HOME + 'search'); await scroll.pending;
  assert.equal(web.active(), false); assert.equal(scroll.enabled, false); assert.equal(scroll.key, key);
  web.inPage(HOME + '?q=chair'); await scroll.pending;
  assert.equal(web.active(), true); assert.equal(scroll.enabled, true); assert.equal(web.insertions.length, 1);
});
test('iframe navigation never changes home scroll compatibility', async () => {
  const web = new FakeWeb(), scroll = new TaobaoHomeScroll(web); await scroll.pending;
  const scripts = web.scripts.length; web.emit('did-navigate-in-page', {}, PRODUCT, false); await scroll.pending;
  assert.equal(web.scripts.length, scripts); assert.equal(web.active(), true);
});
test('a late insertion after document replacement cannot activate a product page', async () => {
  const web = new FakeWeb(), gate = deferred(), started = deferred();
  web.beforeInsert = async () => { started.resolve(); await gate.promise; };
  const scroll = new TaobaoHomeScroll(web); await started.promise;
  web.navigate(PRODUCT); gate.resolve(); await scroll.pending;
  assert.equal(scroll.key, null); assert.equal(web.active(), false); assert.equal(web.scripts.length, 0);
});
test('a late home insertion is retried for the new home document', async () => {
  const web = new FakeWeb(), gate = deferred(), started = deferred();
  web.beforeInsert = async () => { started.resolve(); await gate.promise; };
  const scroll = new TaobaoHomeScroll(web); await started.promise;
  web.navigate(HOME + '?new-document=1'); web.beforeInsert = null; gate.resolve(); await scroll.pending;
  assert.equal(web.insertions.length, 2); assert.equal(web.active(), true); assert.equal(scroll.key, 'style-2');
});
test('the renderer URL check rejects navigation during asynchronous activation', async () => {
  const web = new FakeWeb(), gate = deferred(), started = deferred();
  web.beforeExecute = async code => { if (code.includes('const active = true')) { started.resolve(); await gate.promise; } };
  const scroll = new TaobaoHomeScroll(web); await started.promise;
  web.inPage(HOME + 'login'); gate.resolve(); await scroll.pending;
  assert.equal(web.active(), false); assert.equal(scroll.enabled, false);
});
test('failed CSS insertion can be retried by the next ready event', async () => {
  const web = new FakeWeb(); web.beforeInsert = async () => { throw new Error('controlled insertion failure'); };
  const scroll = new TaobaoHomeScroll(web); await assert.rejects(scroll.pending, /insertion failure/);
  web.beforeInsert = null; web.emit('did-finish-load'); await scroll.pending;
  assert.equal(web.active(), true); assert.equal(scroll.enabled, true);
});
test('failed attribute activation retries without inserting a duplicate sheet', async () => {
  const web = new FakeWeb(); web.beforeExecute = async () => { throw new Error('controlled activation failure'); };
  const scroll = new TaobaoHomeScroll(web); await assert.rejects(scroll.pending, /activation failure/);
  web.beforeExecute = null; web.emit('dom-ready'); await scroll.pending;
  assert.equal(web.active(), true); assert.equal(web.insertions.length, 1);
});
test('disposal after an in-flight activation always deactivates the home rules', async () => {
  const web = new FakeWeb(), gate = deferred(), started = deferred();
  web.beforeExecute = async code => { if (code.includes('const active = true')) { started.resolve(); await gate.promise; } };
  const scroll = new TaobaoHomeScroll(web); await started.promise;
  scroll.dispose(); gate.resolve(); await scroll.pending;
  assert.equal(web.active(), false); assert.equal(scroll.enabled, false); assert.equal(scroll.key, null);
  for (const event of ['did-navigate', 'dom-ready', 'did-finish-load', 'did-navigate-in-page', 'destroyed']) assert.equal(web.listenerCount(event), 0);
});
test('destroyed webContents do not trigger further CSS or DOM operations', async () => {
  const web = new FakeWeb(), scroll = new TaobaoHomeScroll(web); await scroll.pending;
  const scripts = web.scripts.length, inserted = web.insertions.length;
  web.destroyed = true; web.emit('destroyed'); await scroll.pending; await scroll.requestSync(); scroll.dispose();
  assert.equal(web.scripts.length, scripts); assert.equal(web.insertions.length, inserted); assert.equal(scroll.disposed, true);
});
