'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { YouTubeAuth, loginEvidence } = require('../youtube-auth.cjs');
const cookie = { domain: '.youtube.com', name: 'SID', value: 'fixture', path: '/' };
test('auth evidence reads structured login flags and ignores arbitrary page text', () => {
  assert.equal(loginEvidence('<script>var ytInitialData = {"responseContext":{"mainAppWebResponseContext":{"loggedOut":false}}};</script>'), true);
  assert.equal(loginEvidence('<script>ytcfg.set({"LOGGED_IN":false})</script>'), false);
  assert.equal(loginEvidence('<div>"LOGGED_IN":true</div>'), null);
});
test('no cookies, expired cookies or Google-only cookies cannot enable downloading', async () => {
  for (const cookies of [[], [{ ...cookie, expirationDate: 1 }], [{ ...cookie, domain: '.google.com' }]]) {
    const auth = new YouTubeAuth(async () => cookies, () => { throw new Error('must not request'); }, () => {});
    await assert.rejects(() => auth.ensure(), { code: 'YOUTUBE_AUTH_REQUIRED' });
  }
});
test('cookie presence alone cannot pass; anonymous server response requires Edge login', async () => {
  const auth = new YouTubeAuth(async () => [cookie], async () => false, () => {});
  await assert.rejects(() => auth.ensure(), { code: 'YOUTUBE_AUTH_REQUIRED' }); assert.equal(auth.status, 'expired');
});
test('valid login is cached briefly; logout blocks queued downloads on execution', async () => {
  let cookies = [cookie], requests = 0;
  const auth = new YouTubeAuth(async () => cookies, async () => { requests++; return true; }, () => {});
  await auth.ensure(); await auth.ensure(); assert.equal(requests, 1); assert.equal(auth.status, 'verified');
  cookies = []; await assert.rejects(() => auth.ensure(), { code: 'YOUTUBE_AUTH_REQUIRED' });
});
test('network errors are not mislabeled as expired authentication', async () => {
  const auth = new YouTubeAuth(async () => [cookie], async () => { throw new Error('network unavailable'); }, () => {});
  await assert.rejects(() => auth.ensure(), /network unavailable/); assert.equal(auth.status, 'unverified');
});
test('credential changes during verification cannot authorize a different session', async () => {
  let cookies = [cookie];
  const auth = new YouTubeAuth(async () => cookies, async () => { cookies = [{ ...cookie, value: 'other' }]; return true; }, () => {});
  await assert.rejects(() => auth.ensure(), /状态已更新/); assert.notEqual(auth.status, 'verified');
});
