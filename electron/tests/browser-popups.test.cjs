'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { isTaobaoLoginUrl, nativeTaobaoLoginReturn, taobaoPopupKind } = require('../browser-popups.cjs');

test('Taobao login relays and verification routes are temporary, product pages are not', () => {
  for (const url of ['https://detail.tmall.com/wow/z/app/tbpc/home/_____tmd_____/page/login_jump?rand=temporary',
    'https://login.taobao.com/member/login.jhtml', 'https://login.tmall.com/',
    'https://item.taobao.com/login_jump', 'https://detail.tmall.com/_____tmd_____/punish'])
    assert.equal(isTaobaoLoginUrl(url), true, url);
  for (const url of ['https://detail.tmall.com/item.htm?id=123', 'https://cart.taobao.com/cart.htm',
    'https://www.taobao.com/search?q=login_jump', 'https://www.youtube.com/login_jump'])
    assert.equal(isTaobaoLoginUrl(url), false, url);
});

test('temporary-page detection cannot be spoofed by domains, credentials or URL schemes', () => {
  for (const url of ['https://login.taobao.com.evil.invalid/member/login.jhtml',
    'https://evil.invalid/_____tmd_____/page/login_jump', 'https://name:secret@login.taobao.com/',
    'file:///C:/login_jump', 'javascript:alert(1)', 'data:text/html,login_jump'])
    assert.equal(isTaobaoLoginUrl(url), false, url);
});

test('native popup handling is limited to Taobao sources and validated destinations', () => {
  const source = 'https://detail.tmall.com/item.htm?id=123';
  assert.equal(taobaoPopupKind(source, 'https://login.taobao.com/member/login.jhtml'), 'login');
  assert.equal(taobaoPopupKind(source, 'https://item.taobao.com/item.htm?id=456'), 'tab');
  assert.equal(taobaoPopupKind(source, 'about:blank'), 'login');
  for (const destination of ['file:///C:/secret', 'javascript:alert(1)', 'https://name:secret@login.taobao.com/',
    'https://www.youtube.com/watch?v=123', 'https://login.taobao.com.evil.invalid/login_jump'])
    assert.equal(taobaoPopupKind(source, destination), null);
  for (const otherSource of ['https://www.youtube.com/', 'https://taobao.com.evil.invalid/', 'about:blank'])
    assert.equal(taobaoPopupKind(otherSource, 'https://login.taobao.com/member/login.jhtml'), null);
});

test('login returns remain native only between validated Taobao pages', () => {
  const product = 'https://detail.tmall.com/item.htm?id=123';
  for (const source of [
    'https://h5api.m.tmall.com/h5/mtop.alibaba.detail.getaddresslist/1.0/_____tmd_____/page/login_jump',
    'https://login.taobao.com/member/login.jhtml',
    'https://detail.tmall.com/_____tmd_____/page/login_jump',
  ]) {
    assert.equal(nativeTaobaoLoginReturn(source, product), true);
    assert.equal(nativeTaobaoLoginReturn(source, 'https://item.taobao.com/item.htm?id=456'), true);
    assert.equal(nativeTaobaoLoginReturn(source, 'https://www.taobao.com/'), true);
    for (const target of [
      'https://www.youtube.com/watch?v=123', 'https://detail.tmall.com.evil.invalid/item.htm?id=123',
      'https://name:secret@detail.tmall.com/item.htm?id=123', 'https://detail.tmall.com:8123/item.htm?id=123',
      'file:///C:/product', 'javascript:alert(1)', 'https://login.taobao.com/member/login.jhtml',
    ]) assert.equal(nativeTaobaoLoginReturn(source, target), false, target);
  }
  for (const source of [
    product, 'https://www.taobao.com/', 'https://login.taobao.com.evil.invalid/member/login.jhtml',
    'https://name:secret@login.taobao.com/member/login.jhtml', 'https://login.taobao.com:8123/member/login.jhtml',
    'https://www.youtube.com/login_jump', 'about:blank',
  ]) assert.equal(nativeTaobaoLoginReturn(source, product), false, source);
});
