'use strict';
const { contentKey } = require('./model.cjs');

function findDetails(root, id = null, maximum = 24, budget = { remaining: 20000 }) {
  const seen = new WeakSet(), ids = new Set(), details = [];
  const videoFields = ['width','height','duration','play_addr','playAddr','play_addr_h264','playAddrH264',
    'play_addr_bytevc1','playAddrBytevc1','download_addr','downloadAddr','bit_rate','bitRateList',
    'cover','origin_cover','originCover','is_h265','is_bytevc1'];
  function hasAddress(value, depth = 0) {
    if (depth > 3 || !value) return false;
    if (typeof value === 'string') return /^(?:https?:)?\/\//i.test(value);
    if (Array.isArray(value)) return value.slice(0, 8).some(row => hasAddress(row, depth + 1));
    return typeof value === 'object' && hasAddress(value.url_list || value.urlList || value.src || value.url, depth + 1);
  }
  function hasVideo(video) {
    const rates = video.bit_rate || video.bitRateList;
    return ['play_addr','playAddr','play_addr_h264','playAddrH264','play_addr_bytevc1','playAddrBytevc1','download_addr','downloadAddr']
      .some(key => hasAddress(video[key])) ||
      (Array.isArray(rates) ? rates : []).slice(0, 100).some(row => row &&
        String(row.format || row.videoFormat || '').toLowerCase() !== 'dash' && hasAddress(row.play_addr || row.playAddr));
  }
  function walk(value, depth) {
    if (!value || typeof value !== 'object' || depth > 16 || budget.remaining-- <= 0 || seen.has(value) || details.length >= maximum) return;
    seen.add(value);
    const videoId = String(value.aweme_id || value.awemeId || value.id || '');
    if (/^\d{1,32}$/.test(videoId) && (!id || videoId === id) && !ids.has(videoId) && value.video && typeof value.video === 'object' && hasVideo(value.video)) {
      const author = value.author || {};
      const detail = { aweme_id: videoId, desc: String(value.desc || value.title || '').slice(0, 4000),
        author: { nickname: author.nickname, unique_id: author.unique_id || author.uniqueId,
          uid: author.uid, sec_uid: author.sec_uid || author.secUid },
        video: Object.fromEntries(videoFields.filter(key => value.video[key] != null).map(key => [key, value.video[key]])),
      };
      try { const json = JSON.stringify(detail); if (json.length <= 500000) { details.push(JSON.parse(json)); ids.add(videoId); return; } } catch {}
    }
    for (const child of Object.values(value).slice(0, 500)) {
      if (budget.remaining <= 0 || details.length >= maximum) break;
      walk(child, depth + 1);
    }
  }
  walk(root, 0); return details;
}
function findDetail(root, id, budget) {
  return findDetails(root, id, 1, budget)[0] || null;
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
  const findDetails = ${findDetails.toString()};
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
    this.web = web; this.pending = new Set(); this.cache = new Map(); this.owned = false; this.generation = 0;
    this.listener = (_event, method, params) => this.message(method, params).catch(() => {});
    try {
      if (web.debugger.isAttached()) return;
      web.debugger.attach('1.3'); this.owned = true;
      web.debugger.on('message', this.listener);
      web.debugger.sendCommand('Network.enable', { maxResourceBufferSize: 2000000, maxTotalBufferSize: 4000000 }).catch(() => {});
    } catch {}
  }
  reset() { this.generation++; this.pending.clear(); this.cache.clear(); }
  remember(root) {
    for (const detail of findDetails(root)) {
      const context = { videoId: detail.aweme_id, detail }, bytes = Buffer.byteLength(JSON.stringify(context));
      this.cache.delete(context.videoId); this.cache.set(context.videoId, { context, bytes, at: Date.now() });
    }
    while (this.cache.size > 24 || [...this.cache.values()].reduce((sum, row) => sum + row.bytes, 0) > 3000000)
      this.cache.delete(this.cache.keys().next().value);
  }
  async message(method, params) {
    if (method === 'Network.responseReceived') {
      const url = new URL(params.response.url);
      if (url.protocol === 'https:' && url.hostname === 'www.douyin.com' &&
          /^\/aweme\/v1\/web\/(?:aweme\/|feed\/|tab\/feed\/)/.test(url.pathname) &&
          params.response.status === 200 && /json/.test(params.response.mimeType) && this.pending.size < 20)
        this.pending.add(params.requestId);
    } else if (method === 'Network.loadingFailed') this.pending.delete(params.requestId);
    else if (method === 'Network.loadingFinished' && this.pending.delete(params.requestId) && params.encodedDataLength <= 2000000) {
      const generation = this.generation;
      const response = await this.web.debugger.sendCommand('Network.getResponseBody', { requestId: params.requestId });
      const body = response.base64Encoded ? Buffer.from(response.body, 'base64').toString('utf8') : response.body;
      if (body.length > 2000000 || this.generation !== generation || this.web.isDestroyed()) return;
      // Feed requests prefetch videos before their modal URL becomes current.
      this.remember(JSON.parse(body));
    }
  }
  get(url) {
    const id = contentKey(url)?.match(/^douyin:(\d+)$/)?.[1], row = this.cache.get(id);
    if (row && Date.now() - row.at > 300000) { this.cache.delete(id); return null; }
    return row?.context || null;
  }
  dispose() {
    this.reset();
    if (this.owned && !this.web.isDestroyed()) {
      this.web.debugger.removeListener('message', this.listener);
      if (this.web.debugger.isAttached()) this.web.debugger.detach();
    }
  }
}
module.exports = { DouyinPage, snapshotScript, findDetails, findDetail, findInFlight };
