'use strict';
const { randomUUID } = require('node:crypto');

const SITES = {
  youtube: { name: 'YouTube', home: 'https://www.youtube.com/', domains: ['youtube.com', 'google.com', 'youtu.be'], auth: ['SID', 'SAPISID', 'LOGIN_INFO', '__Secure-3PAPISID', '__Secure-1PSID'] },
  bilibili: { name: 'B站', home: 'https://www.bilibili.com/', domains: ['bilibili.com', 'b23.tv'], auth: ['SESSDATA'] },
  xiaohongshu: { name: '小红书', home: 'https://www.xiaohongshu.com/explore', domains: ['xiaohongshu.com', 'xhslink.com'], auth: ['web_session'] },
};
function belongs(host, domain) { return host === domain || host.endsWith('.' + domain); }
function navigationUrl(value) {
  let text = String(value || '').trim();
  if (!/^[a-z][a-z\d+.-]*:/i.test(text)) text = 'https://' + text;
  const url = new URL(text);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('请输入正常的 http 或 https 网页地址');
  return url.href;
}
function siteForUrl(value) {
  try {
    const url = new URL(value);
    if (!['https:', 'http:'].includes(url.protocol)) return null;
    for (const [site, config] of Object.entries(SITES)) {
      if (config.domains.filter(d => d !== 'google.com').some(d => belongs(url.hostname, d))) return site;
    }
  } catch {}
  return null;
}
function contentKey(value) {
  try {
    const url = new URL(value), site = siteForUrl(value), path = url.pathname;
    if (site === 'youtube') {
      const id = url.hostname === 'youtu.be' ? path.slice(1) : url.searchParams.get('v') || path.match(/^\/(?:shorts|live|embed)\/([^/]+)/)?.[1];
      if (id) return `youtube:${id}`;
      if (path === '/playlist' && url.searchParams.get('list')) return `youtube:list:${url.searchParams.get('list')}`;
    }
    if (site === 'bilibili') {
      if (url.hostname === 'b23.tv' && path.length > 1) return value;
      const id = path.match(/\/(?:video|bangumi\/play)\/([^/]+)/)?.[1];
      if (id) return `bilibili:${id}:${url.searchParams.get('p') || 1}`;
    }
    if (site === 'xiaohongshu') {
      if (belongs(url.hostname, 'xhslink.com') && path.length > 1) return value;
      const id = path.match(/\/(?:explore|discovery\/item|user\/profile\/[^/]+)\/([\da-f]{16,32})/i)?.[1];
      if (id) return `xiaohongshu:${id.toLowerCase()}`;
    }
  } catch {}
  return null;
}
function cookieAllowed(cookie, site) {
  const domain = String(cookie.domain || '').replace(/^\./, '').toLowerCase();
  return SITES[site]?.domains.some(d => belongs(domain, d)) || false;
}
function hasAuth(cookies, site) {
  return cookies.some(c => cookieAllowed(c, site) && SITES[site].auth.includes(c.name) && c.value &&
    (!c.expirationDate || c.expirationDate > Date.now() / 1000) &&
    belongs(String(c.domain).replace(/^\./, ''), site === 'youtube' ? 'youtube.com' : site === 'bilibili' ? 'bilibili.com' : 'xiaohongshu.com'));
}
function cookieText(cookies, site) {
  const lines = ['# Netscape HTTP Cookie File', '# Local task snapshot; never share this file.'];
  for (const cookie of cookies.filter(c => cookieAllowed(c, site))) {
    const fields = [cookie.domain, cookie.domain.startsWith('.') ? 'TRUE' : 'FALSE', cookie.path || '/',
      cookie.secure ? 'TRUE' : 'FALSE', String(Math.max(0, Math.floor(cookie.expirationDate || 0))), cookie.name, cookie.value];
    if (fields.some(field => /[\r\n\t]/.test(String(field)))) continue;
    if (cookie.httpOnly) fields[0] = '#HttpOnly_' + fields[0];
    lines.push(fields.join('\t'));
  }
  return lines.join('\n') + '\n';
}
function readCookies(text, site) {
  const result = [];
  for (let line of text.split(/\r?\n/)) {
    const httpOnly = line.startsWith('#HttpOnly_');
    if (httpOnly) line = line.slice(10);
    else if (line.startsWith('#')) continue;
    const fields = line.split('\t');
    if (fields.length !== 7) continue;
    const [domain, , cookiePath, secure, expiry, name, value] = fields;
    const cookie = { domain, path: cookiePath, secure: secure === 'TRUE', httpOnly, name, value };
    if (!cookieAllowed(cookie, site) || !name) continue;
    const expires = Number(expiry);
    if (expires > 0 && expires <= Date.now() / 1000) continue;
    if (expires > 0) cookie.expirationDate = expires;
    cookie.url = `${cookie.secure ? 'https' : 'http'}://${domain.replace(/^\./, '')}${cookiePath || '/'}`;
    result.push(cookie);
  }
  return result;
}
class DownloadQueue {
  constructor(run, changed) { this.run = run; this.changed = changed; this.tasks = []; this.active = null; this.closing = false; }
  add(spec, title) {
    const task = { id: randomUUID(), title, status: 'queued', percent: 0, logs: [], createdAt: Date.now(), spec };
    this.tasks.unshift(task); this.changed(); this.pump(); return task.id;
  }
  list() { return this.tasks.slice(0, 60).map(({ spec, process, ...task }) => task); }
  async pump() {
    if (this.active || this.closing) return;
    const task = [...this.tasks].reverse().find(t => t.status === 'queued');
    if (!task) return;
    this.active = task; task.status = 'running'; this.changed();
    try {
      task.process = this.run(task.spec, (type, data) => {
        if (type === 'log') task.logs = [...task.logs, String(data)].slice(-35);
        if (type === 'progress') { task.percent = Number(data.percent) || 0; task.detail = data.status; }
        this.changed();
      });
      task.result = await task.process.promise;
      task.status = task.status === 'cancelling' ? 'cancelled' : 'completed';
      task.percent = task.status === 'completed' ? 100 : task.percent;
    } catch (error) {
      task.status = task.status === 'cancelling' || error.cancelled ? 'cancelled' : 'failed'; task.error = error.message;
    } finally {
      delete task.process; this.active = null; this.changed(); this.pump();
    }
  }
  cancel(id) {
    const task = this.tasks.find(t => t.id === id);
    if (!task) return;
    if (task.status === 'queued') task.status = 'cancelled';
    else if (task.status === 'running') { task.status = 'cancelling'; task.process?.cancel(); }
    this.changed();
  }
  retry(id) {
    const task = this.tasks.find(t => t.id === id);
    if (!task?.spec || !['failed', 'cancelled'].includes(task.status)) throw new Error('请重新解析该内容后再加入下载队列');
    return this.add(task.spec, task.title);
  }
  close() { this.closing = true; this.tasks.forEach(t => this.cancel(t.id)); }
}
module.exports = { SITES, belongs, navigationUrl, siteForUrl, contentKey, cookieAllowed, cookieText, readCookies, hasAuth, DownloadQueue };
