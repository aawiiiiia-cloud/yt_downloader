'use strict';
const { randomUUID } = require('node:crypto');
const path = require('node:path');
const { classifyFailure, describeFailure, safeDetail } = require('./task-failure.cjs');

const SITES = {
  youtube: { name: 'YouTube', home: 'https://www.youtube.com/', domains: ['youtube.com', 'google.com', 'youtu.be'], auth: ['SID', 'SAPISID', 'LOGIN_INFO', '__Secure-3PAPISID', '__Secure-1PSID'] },
  bilibili: { name: 'B站', home: 'https://www.bilibili.com/', domains: ['bilibili.com', 'b23.tv'], auth: ['SESSDATA'] },
  xiaohongshu: { name: '小红书', home: 'https://www.xiaohongshu.com/explore', domains: ['xiaohongshu.com', 'xhslink.com'], auth: ['web_session'] },
  vimeo: { name: 'Vimeo', home: 'https://vimeo.com/', domains: ['vimeo.com'], auth: ['vimeo'] },
  douyin: { name: '抖音', home: 'https://www.douyin.com/', domains: ['douyin.com', 'iesdouyin.com', 'amemv.com'], auth: ['sessionid', 'sessionid_ss', 'sid_guard', 'sid_tt'] },
  // Behance also uses Adobe browser storage; anonymous bcp cookies are not login proof.
  behance: { name: 'Behance', home: 'https://www.behance.net/', domains: ['behance.net'], auth: [] },
  taobao: { name: '淘宝', home: 'https://www.taobao.com/', domains: ['taobao.com', 'tmall.com'], authDomains: ['taobao.com', 'tmall.com'], auth: ['cookie17', 'sgcookie'] },
  // The official frontend requires both cookies; JSESSIONID/z_ui alone are anonymous/profile state.
  zcool: { name: '站酷', home: 'https://www.zcool.com.cn/', domains: ['zcool.com.cn'], cookieDomains: ['zcool.com.cn', 'zcool.cn'],
    auth: ['zcool_mt', 'zcool_logon_new'], authAll: true },
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
    if (site === 'vimeo') {
      const match = path.match(/^\/(?:video\/|channels\/[^/]+\/|groups\/[^/]+\/videos\/|ondemand\/[^/]+\/)?(\d+)(?:\/([\da-f]+))?\/?$/i);
      if (match) return `vimeo:${match[1]}:${match[2] || url.searchParams.get('h') || ''}`;
    }
    if (site === 'behance' && ['www.behance.net', 'behance.net'].includes(url.hostname)) {
      const id = path.match(/^\/gallery\/(\d+)(?:\/|$)/)?.[1];
      if (id) return `behance:${id}`;
    }
    if (site === 'taobao' && ['item.taobao.com', 'detail.tmall.com', 'chaoshi.detail.tmall.com'].includes(url.hostname) && path === '/item.htm') {
      const id = url.searchParams.get('id');
      if (/^\d{1,30}$/.test(id || '')) return `taobao:${id}`;
    }
    if (site === 'douyin') {
      if (url.hostname === 'v.douyin.com' && /^\/[\w-]+\/?$/.test(path)) return `douyin:short:${path.replace(/\//g, '')}`;
      const id = url.searchParams.get('modal_id') || path.match(/^\/(?:video|share\/video)\/(\d+)/)?.[1];
      if (/^\d+$/.test(id || '')) return `douyin:${id}`;
    }
    if (site === 'zcool' && ['www.zcool.com.cn', 'zcool.com.cn'].includes(url.hostname) &&
        !url.username && !url.password && ['', '80', '443'].includes(url.port)) {
      const id = decodeURIComponent(path).match(/^\/work\/(Z[A-Za-z0-9_+=-]{5,100})\.html$/)?.[1];
      if (id) return `zcool:${id}`;
    }
  } catch {}
  return null;
}
function cookieAllowed(cookie, site) {
  const domain = String(cookie.domain || '').replace(/^\./, '').toLowerCase();
  return (SITES[site]?.cookieDomains || SITES[site]?.domains)?.some(d => belongs(domain, d)) || false;
}
function hasAuth(cookies, site) {
  const config = SITES[site];
  if (!config?.auth.length) return false;
  const names = new Set(cookies.filter(c => cookieAllowed(c, site) && config.auth.includes(c.name) && c.value &&
    (!c.expirationDate || c.expirationDate > Date.now() / 1000) &&
    (config.authDomains || [config.domains[0]]).some(domain => belongs(String(c.domain).replace(/^\./, ''), domain))).map(c => c.name));
  return config.authAll ? config.auth.every(name => names.has(name)) : names.size > 0;
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
const DEFAULT_CONCURRENCY = { globalConcurrency: 5, siteConcurrency: 1 };
function concurrencySettings(values = {}, strict = false) {
  return Object.fromEntries(Object.entries(DEFAULT_CONCURRENCY).map(([key, fallback]) => {
    const value = values[key];
    if (Number.isInteger(value) && value >= 1 && value <= 20) return [key, value];
    if (strict) throw new Error(`${key === 'globalConcurrency' ? '全局并发' : '单站并发'}请填写 1–20 之间的整数`);
    return [key, fallback];
  }));
}
function downloadTargetKey(spec) {
  let key = contentKey(spec?.url);
  if (!key) return null;
  // Alternate parts/access hashes can still write the same video filename.
  if (key.startsWith('bilibili:') || key.startsWith('vimeo:')) key = key.split(':').slice(0, 2).join(':');
  // A product's image categories have separate folders and separate manifests.
  if (key.startsWith('taobao:') && spec.mediaId) key += ':' + spec.mediaId;
  if (key.startsWith('zcool:') && spec.kind === 'video' && spec.mediaId) key += ':' + spec.mediaId;
  let directory = path.resolve(spec.settings?.saveDir || '.');
  if (process.platform === 'win32') directory = directory.toLowerCase();
  return JSON.stringify([directory, key]);
}
function downloadsConflict(left, right) {
  const target = downloadTargetKey(left);
  if (!target || target !== downloadTargetKey(right)) return false;
  const indices = spec => spec.kind === 'images' && Array.isArray(spec.indices) && spec.indices.length &&
    spec.indices.every(index => Number.isInteger(index) && index > 0) ? new Set(spec.indices) : null;
  const a = indices(left), b = indices(right);
  // Unknown selections and videos keep the conservative whole-output lock.
  return !a || !b || [...a].some(index => b.has(index));
}
const pendingStatus = status => ['queued', 'running', 'cancelling'].includes(status);
class DownloadQueue {
  constructor(run, changed, settings = {}) {
    this.run = run; this.changed = changed; this.tasks = []; this.active = new Set(); this.closing = false;
    this.limits = concurrencySettings(settings);
  }
  setLimits(settings) {
    this.limits = concurrencySettings(settings, true); this.changed(); this.pump();
  }
  add(spec, title) {
    const task = { id: randomUUID(), title, status: 'queued', percent: 0, logs: [], createdAt: Date.now(), spec };
    this.tasks.unshift(task); this.changed(); this.pump(); return task.id;
  }
  list() {
    // A long-running task must remain visible while other sites finish newer jobs.
    const pending = this.tasks.filter(task => pendingStatus(task.status));
    const recent = this.tasks.filter(task => !pendingStatus(task.status)).slice(0, Math.max(0, 60 - pending.length));
    const visible = new Set([...pending, ...recent]);
    return this.tasks.filter(task => visible.has(task)).map(({ spec, process, ...task }) => task);
  }
  clearFinished() {
    const before = this.tasks.length;
    this.tasks = this.tasks.filter(task => this.active.has(task) || !['completed', 'failed', 'cancelled'].includes(task.status));
    const removed = before - this.tasks.length;
    if (removed) this.changed();
    return removed;
  }
  canStart(task) {
    const site = siteForUrl(task.spec?.url);
    let count = 0;
    for (const running of this.active) {
      if (siteForUrl(running.spec?.url) === site) count++;
      if (downloadsConflict(task.spec, running.spec)) return false;
    }
    // Do not let later single-image jobs repeatedly overtake a waiting batch
    // that contains their image. Unrelated images/sites may still fill slots.
    for (const earlier of [...this.tasks].reverse()) {
      if (earlier === task) break;
      if (earlier.status === 'queued' && downloadsConflict(task.spec, earlier.spec)) return false;
    }
    return count < this.limits.siteConcurrency;
  }
  pump() {
    if (this.closing || this.pumping) return;
    this.pumping = true;
    try {
      while (!this.closing && this.active.size < this.limits.globalConcurrency) {
        const task = [...this.tasks].reverse().find(row => row.status === 'queued' && this.canStart(row));
        if (!task) break;
        this.active.add(task); task.status = 'running'; task.percent = null; task.detail = 'preparing'; task.speed = null; task.speedUpdatedAt = null; task.eta = null;
        task.logs = [...task.logs, '准备下载，正在检查登录资料与网络连接…'].slice(-35);
        this.changed(); this.start(task);
      }
    } finally { this.pumping = false; }
  }
  async start(task) {
    try {
      task.process = this.run(task.spec, (type, data) => {
        if (type === 'log') task.logs = [...task.logs, safeDetail(data)].slice(-35);
        if (type === 'progress') {
          const percent = Number(data.percent);
          task.percent = data.percent == null || !Number.isFinite(percent) ? null : Math.max(0, Math.min(100, percent));
          task.detail = data.status; task.downloadedBytes = Math.max(0, Number(data.downloadedBytes) || 0);
          const speed = Number(data.speed), eta = Number(data.eta);
          const receiving = ['downloading', 'image-receiving'].includes(data.status);
          task.speed = receiving && data.speed != null && Number.isFinite(speed) && speed >= 0 ? speed : null;
          task.speedUpdatedAt = task.speed == null ? null : Date.now();
          task.eta = receiving && data.eta != null && Number.isFinite(eta) && eta >= 0 ? eta : null;
        }
        this.changed();
      });
      task.result = await task.process.promise;
      task.status = task.status === 'cancelling' ? 'cancelled' : 'completed';
      if (task.status === 'cancelled') task.error = describeFailure('CANCELLED').message;
      task.percent = task.status === 'completed' ? 100 : task.percent;
    } catch (error) {
      task.status = task.status === 'cancelling' || error.cancelled ? 'cancelled' : 'failed';
      const failure = task.status === 'cancelled' ? describeFailure('CANCELLED') : classifyFailure(error, task.logs);
      task.failure = failure; task.error = failure.message;
      if (failure.detail && failure.detail !== failure.message) task.logs = [...task.logs, failure.detail].slice(-35);
    } finally {
      task.speed = null; task.speedUpdatedAt = null; task.eta = null;
      delete task.process; this.active.delete(task); this.changed(); this.pump();
    }
  }
  cancel(id) {
    const task = this.tasks.find(t => t.id === id);
    if (!task) return;
    if (task.status === 'queued') { task.status = 'cancelled'; task.error = describeFailure('CANCELLED').message; }
    else if (task.status === 'running') { task.status = 'cancelling'; task.process?.cancel(); }
    this.changed();
    this.pump();
  }
  retry(id) {
    const task = this.tasks.find(t => t.id === id);
    if (!task?.spec || !['failed', 'cancelled'].includes(task.status)) throw new Error('请重新解析该内容后再加入下载队列');
    return this.add(task.spec, task.title);
  }
  close() { this.closing = true; this.tasks.forEach(t => this.cancel(t.id)); }
}
module.exports = { SITES, belongs, navigationUrl, siteForUrl, contentKey, cookieAllowed, cookieText, readCookies, hasAuth, concurrencySettings, downloadTargetKey, downloadsConflict, DownloadQueue };
