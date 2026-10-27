'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const { WEB_LOGIN, loginPlan, loginHost, surfaceScript, waitForLogin } = require('../web-login.cjs');

test('embedded login covers all six supported sites and excludes external-only sites', () => {
  assert.equal(Object.keys(WEB_LOGIN).length, 6);
  for (const site of Object.keys(WEB_LOGIN)) {
    assert.equal(new URL(loginPlan(site).url).protocol, 'https:'); new Function(surfaceScript(site));
  }
  for (const site of ['youtube', 'vimeo', '__proto__', 'unknown']) assert.throws(() => loginPlan(site));
  assert.match(loginPlan('zcool').url, /passport\.zcool\.com\.cn\/loginApp\.do/);
});

test('authentication host checks reject lookalikes and insecure URLs', () => {
  assert.ok(loginHost('behance', 'https://auth.services.adobe.com/zh_CN/index.html'));
  assert.ok(loginHost('behance', 'https://auth-light.identity.adobe.com/sentry/iframe/index.html'));
  assert.ok(loginHost('zcool', 'https://passport.zcool.com.cn/login.do'));
  for (const url of ['http://passport.zcool.com.cn/login.do', 'https://passport.zcool.com.cn.evil.test/login.do', 'https://evil.test/?url=https://passport.zcool.com.cn', 'about:blank']) assert.ok(!loginHost('zcool', url));
  assert.ok(!loginHost('behance', 'https://www.behance.net/login'));
});

test('login opening waits for hydration and verifies the actual modal after one click', async () => {
  let probes = 0, clicks = 0;
  await waitForLogin({ intervalMs: 1, timeoutMs: 200, active: () => true,
    probe: async () => ({ ready: ++probes >= 5, canTrigger: probes >= 2 }), trigger: async () => clicks++ });
  assert.equal(clicks, 1); assert.equal(probes, 5);
});

test('an already visible login form is not clicked or submitted', async () => {
  await waitForLogin({ active: () => true, probe: async () => ({ ready: true, canTrigger: true }),
    trigger: async () => assert.fail('must not click an existing login form') });
});

test('switching tabs during a read cancels before any trigger', async () => {
  let active = true;
  await assert.rejects(waitForLogin({ active: () => active, probe: async () => { active = false; return { canTrigger: true }; },
    trigger: async () => assert.fail('stale tab must not be clicked') }), { cancelled: true });
});

test('a missing login entry times out without clicking arbitrary form buttons', async () => {
  await assert.rejects(waitForLogin({ intervalMs: 1, timeoutMs: 10, active: () => true, probe: async () => ({}),
    trigger: async () => assert.fail('no known trigger') }), /未能打开登录界面/);
});

test('a trigger that does not open a form is reported instead of repeatedly clicked', async () => {
  let clicks = 0;
  await assert.rejects(waitForLogin({ intervalMs: 1, timeoutMs: 20, active: () => true,
    probe: async () => ({ canTrigger: true }), trigger: async () => clicks++ }), /未能打开登录界面/);
  assert.equal(clicks, 1);
});
