'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const { LoginState } = require('../login-state.cjs');
const { SITES } = require('../model.cjs');
test('all sites keep rejected credentials marked until actual authentication cookies change', () => {
  for (const [site, config] of Object.entries(SITES)) {
    if (!config.auth.length) continue; // Behance uses Adobe storage; no cookie-only login claim.
    const names = config.authAll ? config.auth : [config.auth[0]];
    const cookies = names.map(name => ({ domain: '.' + config.domains[0], path: '/', name, value: 'old-auth' }));
    const state = new LoginState(); state.observe(cookies);
    assert.ok(state.state().detected[site]); state.reject(site, state.keys[site]);
    state.observe([...cookies, { ...cookies[0], name: 'analytics', value: 'new-analytics' }]); assert.ok(state.state().expired[site]);
    const restored = new LoginState(JSON.parse(JSON.stringify(state.invalid))); restored.observe(cookies); assert.ok(restored.state().expired[site]);
    restored.observe(cookies.map(cookie => ({ ...cookie, value: 'new-auth' }))); assert.ok(!restored.state().expired[site]);
    assert.doesNotMatch(JSON.stringify(state.invalid), /old-auth/);
  }
});
test('a failing old download cannot invalidate credentials imported while it was running', () => {
  const cookie = { domain: '.vimeo.com', path: '/', name: 'vimeo', value: 'old' };
  const state = new LoginState(); state.observe([cookie]); const oldKey = state.keys.vimeo;
  state.observe([{ ...cookie, value: 'new' }]); assert.equal(state.reject('vimeo', oldKey), false); assert.ok(!state.state().expired.vimeo);
  state.reject('vimeo', state.keys.vimeo); state.accept('vimeo', oldKey); assert.ok(state.state().expired.vimeo);
});
test('absent credentials show signed out, and successful verification can restore a previously rejected session', () => {
  const cookie = { domain: '.youtube.com', path: '/', name: 'SID', value: 'fixture' };
  const state = new LoginState(); state.observe([cookie]); state.reject('youtube', state.keys.youtube);
  state.accept('youtube', state.keys.youtube); assert.ok(!state.state().expired.youtube);
  state.observe([]); assert.ok(!state.state().detected.youtube); assert.ok(!state.state().expired.youtube);
});
