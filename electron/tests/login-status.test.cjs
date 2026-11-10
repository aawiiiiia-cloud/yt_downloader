'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const { display } = require('../ui/login-status.js');

test('ordinary sites use the unified missing and detected credential assertions', () => {
  for (const site of ['bilibili','xiaohongshu','douyin','behance','taobao','zcool']) {
    let row = display(site, site, false, false);
    assert.equal(row.text, `${site} · 【未检测到登录凭据】建议登录`);
    assert.equal(row.warning, true); assert.equal(row.success, false);
    row = display(site, site, true, false);
    assert.equal(row.text, `${site} · 【已检测到登录凭据】如左侧网页显示未登录，请重新登录`);
    assert.equal(row.warning, false); assert.equal(row.success, false);
  }
});
test('YouTube and Vimeo direct both assertions to the Edge login action', () => {
  for (const site of ['youtube','vimeo']) {
    const name = site === 'youtube' ? 'YouTube' : 'Vimeo';
    let row = display(site, name, false, false);
    assert.equal(row.text, `${name} · 【未检测到登录凭据】请使用下方Edge登录`);
    assert.equal(row.warning, true); assert.equal(row.success, false);
    row = display(site, name, true, false);
    assert.equal(row.text, `${name} · 【已检测到登录凭据】如左侧网页显示未登录，请重新使用下方Edge登录`);
    assert.equal(row.warning, false); assert.equal(row.success, false);
  }
});
test('inconclusive verification phases retain the detected credential assertion', () => {
  for (const status of ['unverified','checking','verified']) {
    const row = display('youtube','YouTube',true,status === 'expired',status);
    assert.match(row.text,/【已检测到登录凭据】/); assert.equal(row.success,false);
  }
});
test('known-invalid credentials explicitly request a new login', () => {
  const row=display('zcool','站酷',true,true);
  assert.equal(row.text,'站酷 · 凭据已失效，请重新登录');
  assert.equal(row.warning,true); assert.equal(row.success,false);
});
