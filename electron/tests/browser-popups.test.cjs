'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { isTaobaoLoginUrl, taobaoPopupKind } = require('../browser-popups.cjs');

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
