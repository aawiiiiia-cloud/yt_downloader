'use strict';
const { createHash } = require('node:crypto');
const { hasAuth, SITES } = require('./model.cjs');
function fingerprint(cookies) {
  if (!hasAuth(cookies, 'youtube')) return '';
  const values = cookies.filter(cookie => (cookie.domain.replace(/^\./, '') === 'youtube.com' || cookie.domain.endsWith('.youtube.com')) && SITES.youtube.auth.includes(cookie.name))
    .map(cookie => [cookie.domain, cookie.path, cookie.name, cookie.value]).sort();
  return createHash('sha256').update(JSON.stringify(values)).digest('hex');
}
function jsonAt(text, start) {
  let depth = 0, quoted = false, escaped = false;
  for (let i = start; i < text.length; i++) {
    const char = text[i];
    if (quoted) { if (escaped) escaped = false; else if (char === '\\') escaped = true; else if (char === '"') quoted = false; }
    else if (char === '"') quoted = true;
    else if (char === '{') depth++;
    else if (char === '}' && --depth === 0) { try { return JSON.parse(text.slice(start, i + 1)); } catch { return null; } }
  }
  return null;
}
function loginEvidence(html) {
  for (const match of html.matchAll(/(?:\b(?:var\s+)?ytInitialData\s*=\s*|window\["ytInitialData"\]\s*=\s*)(?=\{)/g)) {
    const data = jsonAt(html, match.index + match[0].length);
    const loggedOut = data?.responseContext?.mainAppWebResponseContext?.loggedOut;
    if (typeof loggedOut === 'boolean') return !loggedOut;
  }
  for (const match of html.matchAll(/ytcfg\.set\(\s*(?=\{)/g)) {
    const loggedIn = jsonAt(html, match.index + match[0].length)?.LOGGED_IN;
    if (typeof loggedIn === 'boolean') return loggedIn;
  }
  return null;
}
function authRequired(message = '请先通过 Edge 登录 YouTube，再下载视频') {
  return Object.assign(new Error(message), { code: 'YOUTUBE_AUTH_REQUIRED' });
}
class YouTubeAuth {
  constructor(cookies, verify, changed) {
    this.cookies = cookies; this.verify = verify; this.changed = changed; this.key = ''; this.until = 0;
    this.status = 'signed_out'; this.pending = null;
  }
  observe(cookies) {
    const key = fingerprint(cookies);
    if (key !== this.key) { this.key = key; this.until = 0; this.status = key ? 'unverified' : 'signed_out'; }
  }
  async ensure(force = false) {
    this.observe(await this.cookies());
    if (!this.key) { this.changed(); throw authRequired(); }
    if (!force && this.status === 'verified' && Date.now() < this.until) return true;
    if (this.pending) return this.pending;
    const expected = this.key; this.status = 'checking'; this.changed();
    this.pending = (async () => {
      try {
        const result = await this.verify();
        this.observe(await this.cookies());
        if (this.key !== expected) throw new Error('YouTube 登录状态已更新，请再试一次');
        if (result === false) { this.status = 'expired'; throw authRequired('YouTube 登录已失效，请通过 Edge 重新登录'); }
        if (result !== true) throw new Error('暂时无法确认 YouTube 登录状态，请检查网络后重试');
        this.status = 'verified'; this.until = Date.now() + 300000; return true;
      } catch (error) { if (this.status === 'checking') this.status = 'unverified'; throw error; }
      finally { this.pending = null; this.changed(); }
    })();
    return this.pending;
  }
}
module.exports = { YouTubeAuth, loginEvidence, fingerprint, authRequired };
