'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { TaobaoRelayGuard, isTaobaoRelayUrl } = require('../taobao-relay.cjs');
const relay = 'https://detail.tmall.com/wow/z/app/tbpc/home/_____tmd_____/page/login_jump?rand=fixture';
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check) {
  for (let n = 0; n < 50; n++) { if (check()) return; await wait(10); }
  assert.fail('relay guard timed out');
}
function page(probe = async () => true) {
  const web = new EventEmitter();
  Object.assign(web, { url: relay, isDestroyed: () => false, getURL: () => web.url,
    isLoadingMainFrame: () => false, mainFrame: { executeJavaScript: probe } });
  return web;
}
test('only the exact Taobao login callback can be cleaned up automatically', () => {
  assert.ok(isTaobaoRelayUrl(relay));
  for (const url of ['https://login.taobao.com/member/login.jhtml', 'https://detail.tmall.com/_____tmd_____/punish',
    'https://detail.tmall.com/item.htm?id=123&path=/_____tmd_____/page/login_jump',
    'https://detail.tmall.com/_____tmd_____/page/login_jump/verification',
    'https://detail.tmall.com.evil.invalid/_____tmd_____/page/login_jump',
    relay.replace('https://', 'https://name:secret@'), 'file:///C:/_____tmd_____/page/login_jump'])
    assert.equal(isTaobaoRelayUrl(url), false, url);
});
test('cleanup requires two stable empty-document checks after the grace period', async () => {
  let probes = 0, closed = 0;
  const web = page(async () => { probes++; return true; });
  const guard = new TaobaoRelayGuard(web, () => closed++, { delay: 15, settle: 15 });
  try {
    web.emit('dom-ready'); web.emit('did-finish-load');
    assert.equal(closed, 0);
    await until(() => closed === 1); assert.equal(probes, 2);
  } finally { guard.dispose(); }
});
test('content appearing between probes is preserved and ordinary pages are never probed', async () => {
  let probes = 0, closed = 0;
  const web = page(async () => ++probes === 1);
  const guard = new TaobaoRelayGuard(web, () => closed++, { delay: 5, settle: 5 });
  try {
    web.emit('did-finish-load'); await until(() => probes === 2); await wait(20);
    assert.equal(closed, 0);
    web.url = 'https://detail.tmall.com/item.htm?id=123'; web.emit('did-finish-load');
    await wait(20); assert.equal(probes, 2);
  } finally { guard.dispose(); }
});
test('navigation and disposal invalidate pending JavaScript results and release listeners', async () => {
  let resolveProbe, closed = 0;
  const web = page(() => new Promise(resolve => { resolveProbe = resolve; }));
  const guard = new TaobaoRelayGuard(web, () => closed++, { delay: 5, settle: 5 });
  web.emit('did-finish-load'); await until(() => !!resolveProbe);
  web.emit('did-start-navigation', {}, relay, false, true);
  resolveProbe(true); await wait(30); assert.equal(closed, 0);
  web.emit('did-finish-load'); guard.dispose(); await wait(20);
  assert.equal(closed, 0); assert.equal(web.listenerCount('did-start-navigation'), 0);
  assert.equal(web.listenerCount('dom-ready'), 0); assert.equal(web.listenerCount('destroyed'), 0);
});
