'use strict';
const { contentKey } = require('./model.cjs');

function findDetail(root, id) {
  const seen = new WeakSet(); let visits = 0;
  const videoFields = ['width','height','duration','play_addr','playAddr','play_addr_h264','playAddrH264',
    'play_addr_bytevc1','playAddrBytevc1','download_addr','downloadAddr','bit_rate','bitRateList',
    'cover','origin_cover','originCover','is_h265','is_bytevc1'];
  function walk(value, depth) {
    if (!value || typeof value !== 'object' || depth > 16 || visits++ > 20000 || seen.has(value)) return null;
    seen.add(value);
    if (String(value.aweme_id || value.awemeId || value.id) === id && value.video && typeof value.video === 'object') {
      const author = value.author || {};
      const detail = { aweme_id: id, desc: String(value.desc || value.title || '').slice(0, 4000),
        author: { nickname: author.nickname, unique_id: author.unique_id || author.uniqueId,
          uid: author.uid, sec_uid: author.sec_uid || author.secUid },
        video: Object.fromEntries(videoFields.filter(key => value.video[key] != null).map(key => [key, value.video[key]])),
      };
      try { const json = JSON.stringify(detail); return json.length <= 500000 ? JSON.parse(json) : null; } catch { return null; }
    }
    for (const child of Object.values(value).slice(0, 500)) {
      const found = walk(child, depth + 1); if (found) return found;
    }
    return null;
  }
  return walk(root, 0);
}
function findInFlight(records, id) {
  const rows = new Map(); let bytes = 0;
  for (const record of records.slice(0, 200)) {
    const text = Array.isArray(record) && record[1];
    if (typeof text !== 'string' || (bytes += text.length) > 3000000) continue;
    for (const line of text.split('\n').slice(0, 2000)) {
      const colon = line.indexOf(':'); if (colon < 1) continue;
      const key = line.slice(0, colon), value = line.slice(colon + 1);
      if (!/^[\da-f]+$/i.test(key) || !/^[{[]/.test(value)) continue;
      try { rows.set(key, JSON.parse(value)); } catch {}
    }
  }
  let visits = 0;
  function resolve(value, depth = 0, chain = new Set()) {
    if (depth > 20 || visits++ > 50000) return null;
    if (value === '$undefined') return undefined;
    if (typeof value === 'string') {
      const key = value.match(/^\$@?([\da-f]+)$/i)?.[1];
      if (key && rows.has(key) && !chain.has(key)) return resolve(rows.get(key), depth + 1, new Set([...chain, key]));
      return value;
    }
    if (Array.isArray(value)) return value.slice(0, 500).map(child => resolve(child, depth + 1, chain));
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).slice(0, 500).map(([key, child]) => [key, resolve(child, depth + 1, chain)]));
    return value;
  }
  for (const value of rows.values()) { const detail = findDetail(resolve(value), id); if (detail) return detail; }
  return null;
}
const snapshotScript = String.raw`(() => {
  const current = new URL(location.href), id = current.searchParams.get('modal_id') || current.pathname.match(/\/(?:video|share\/video)\/(\d+)/)?.[1];
  if (!/^\d+$/.test(id || '')) return null;
  const findDetail = ${findDetail.toString()};
  const findInFlight = ${findInFlight.toString()};
  const roots = [window._ROUTER_DATA, window.__INITIAL_STATE__, window.__NEXT_DATA__, window.__SSR_DATA__];
  const text = document.getElementById('RENDER_DATA')?.textContent;
  if (text && text.length < 3000000) { try { roots.push(JSON.parse(decodeURIComponent(text))); } catch {} }
  for (const root of roots) { const detail = findDetail(root, id); if (detail) return {videoId: id, detail}; }
  const records = Array.isArray(window.__pace_f) ? [...window.__pace_f] : [];
  for (const script of document.querySelectorAll('script:not([src])')) {
    const text = script.textContent;
    if (text.length > 1500000 || !text.includes('__pace_f.push')) continue;
    const argument = text.match(/__pace_f\.push\((\[[\s\S]*\])\)\s*;?\s*$/)?.[1];
    if (argument) { try { records.push(JSON.parse(argument)); } catch {} }
  }
  const detail = findInFlight(records, id); if (detail) return {videoId: id, detail};
  return null;
})()`;

class DouyinPage {
  constructor(web) {
    this.web = web; this.pending = new Set(); this.cache = new Map(); this.owned = false;
    this.listener = (_event, method, params) => this.message(method, params).catch(() => {});
    try {
      if (web.debugger.isAttached()) return;
      web.debugger.attach('1.3'); this.owned = true;
      web.debugger.on('message', this.listener);
      web.debugger.sendCommand('Network.enable', { maxResourceBufferSize: 2000000, maxTotalBufferSize: 4000000 }).catch(() => {});
    } catch {}
  }
  reset() { this.pending.clear(); this.cache.clear(); }
  currentId() { return contentKey(this.web.getURL())?.match(/^douyin:(\d+)$/)?.[1]; }
  async message(method, params) {
    if (method === 'Network.responseReceived') {
      const url = new URL(params.response.url);
      if (url.protocol === 'https:' && url.hostname === 'www.douyin.com' &&
          /^\/aweme\/v1\/web\/(?:aweme\/|feed\/|tab\/feed\/)/.test(url.pathname) &&
          params.response.status === 200 && /json/.test(params.response.mimeType) && this.pending.size < 20)
        this.pending.add(params.requestId);
    } else if (method === 'Network.loadingFailed') this.pending.delete(params.requestId);
    else if (method === 'Network.loadingFinished' && this.pending.delete(params.requestId) && params.encodedDataLength <= 2000000) {
      const id = this.currentId(); if (!id) return;
      const response = await this.web.debugger.sendCommand('Network.getResponseBody', { requestId: params.requestId });
      const body = response.base64Encoded ? Buffer.from(response.body, 'base64').toString('utf8') : response.body;
      if (body.length > 2000000 || this.currentId() !== id) return;
      const detail = findDetail(JSON.parse(body), id);
      if (detail) {
        this.cache.delete(id); this.cache.set(id, { videoId: id, detail });
        while (this.cache.size > 6) this.cache.delete(this.cache.keys().next().value);
      }
    }
  }
  get(url) { const id = contentKey(url)?.match(/^douyin:(\d+)$/)?.[1]; return this.cache.get(id) || null; }
  dispose() {
    this.reset();
    if (this.owned && !this.web.isDestroyed()) {
      this.web.debugger.removeListener('message', this.listener);
      if (this.web.debugger.isAttached()) this.web.debugger.detach();
    }
  }
}
module.exports = { DouyinPage, snapshotScript, findDetail, findInFlight };
