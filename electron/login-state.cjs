'use strict';
const { SITES } = require('./model.cjs');
const { fingerprint } = require('./youtube-auth.cjs');
class LoginState {
  constructor(saved = {}) {
    this.keys = {};
    this.invalid = Object.fromEntries(Object.entries(saved || {}).filter(([site, value]) => SITES[site] && /^[a-f0-9]{64}$/.test(value)));
  }
  observe(cookies) {
    for (const site of Object.keys(SITES)) {
      const key = fingerprint(cookies, site);
      if (key && this.invalid[site] && key !== this.invalid[site]) delete this.invalid[site];
      this.keys[site] = key;
    }
    return this.state();
  }
  state() {
    return { detected: Object.fromEntries(Object.keys(SITES).map(site => [site, !!this.keys[site]])),
      expired: Object.fromEntries(Object.keys(SITES).map(site => [site, !!this.keys[site] && this.invalid[site] === this.keys[site]])) };
  }
  reject(site, expected) {
    if (!expected || !SITES[site] || this.keys[site] !== expected) return false;
    this.invalid[site] = expected; return true;
  }
  accept(site, expected) {
    if (expected && this.keys[site] === expected) delete this.invalid[site];
  }
}
module.exports = { LoginState };
