'use strict';
const { contentKey } = require('./model.cjs');
const { postSnapshot, completeContext } = require('./instagram-page.cjs');
const MAX_BODY = 4 * 1024 * 1024;

function normalizeMedia(item) {
  if (item.image_versions2 || item.carousel_media) return item;
  const dimensions = item.dimensions || {};
  const images = (item.display_resources || []).slice(0, 32).map(row => ({
    url: row.src, width: row.config_width, height: row.config_height,
  }));
  if (!images.length && item.display_url) images.push({
    url: item.display_url, width: dimensions.width, height: dimensions.height,
  });
  const children = item.edge_sidecar_to_children?.edges;
  return {
    pk: item.id, code: item.shortcode, original_width: dimensions.width, original_height: dimensions.height,
    media_type: Array.isArray(children) ? 8 : item.is_video ? 2 : 1,
    image_versions2: { candidates: images },
    video_versions: item.video_url ? [{ url: item.video_url, ...dimensions }] : [],
    video_duration: item.video_duration, video_dash_manifest: item.video_dash_manifest,
    taken_at: item.taken_at_timestamp, user: item.owner,
    caption: { text: item.edge_media_to_caption?.edges?.[0]?.node?.text },
    carousel_media_count: Array.isArray(children) ? children.length : undefined,
    carousel_media: Array.isArray(children) ? children.slice(0, 101).map(row => normalizeMedia(row.node || {})) : undefined,
  };
}

function publicContexts(root) {
  const stack = [{ value: root, depth: 0 }], seen = new WeakSet(), results = new Map();
  let visits = 0;
  while (stack.length && ++visits <= 60000 && results.size < 24) {
    const { value, depth } = stack.pop();
    if (!value || typeof value !== 'object' || depth > 24 || seen.has(value)) continue;
    seen.add(value);
    const code = value.code || value.shortcode;
    if (/^[A-Za-z0-9_-]{5,32}$/.test(code || '')) {
      const media = normalizeMedia(value), text = JSON.stringify(media);
      if (text.length <= 1400000) {
        const context = postSnapshot({ querySelectorAll: () => [{ textContent: text }] },
          `https://www.instagram.com/p/${code}/`);
        if (completeContext(context)) {
          const previous = results.get(code);
          if (!previous || JSON.stringify(previous).length < JSON.stringify(context).length) results.set(code, context);
          continue;
        }
      }
    }
    for (const child of Object.values(value).slice(0, 500)) {
      if (child && typeof child === 'object' && stack.length + visits < 60000) stack.push({ value: child, depth: depth + 1 });
    }
  }
  return [...results.values()];
}

class InstagramNetwork {
  constructor(web) {
    this.web = web; this.cache = new Map(); this.pending = new Set(); this.generation = 0; this.owned = false;
    this.listener = (_event, method, params) => this.message(method, params).catch(() => {});
    try {
      if (web.debugger.isAttached()) return;
      web.debugger.attach('1.3'); this.owned = true;
      web.debugger.on('message', this.listener);
      web.debugger.sendCommand('Network.enable', { maxResourceBufferSize: MAX_BODY, maxTotalBufferSize: MAX_BODY * 2 }).catch(() => {});
    } catch {}
  }
  reset() { this.generation++; this.pending.clear(); this.cache.clear(); }
  remember(root) {
    for (const context of publicContexts(root)) {
      const key = context.instagram.shortcode, bytes = Buffer.byteLength(JSON.stringify(context));
      this.cache.delete(key); this.cache.set(key, { context, bytes, at: Date.now() });
    }
    while (this.cache.size > 24 || [...this.cache.values()].reduce((sum, row) => sum + row.bytes, 0) > MAX_BODY)
      this.cache.delete(this.cache.keys().next().value);
  }
  async message(method, params) {
    if (method === 'Network.responseReceived') {
      const response = params.response, url = new URL(response.url);
      if (url.protocol === 'https:' && ['www.instagram.com', 'instagram.com', 'i.instagram.com'].includes(url.hostname) &&
          !url.username && !url.password && !url.port &&
          /^\/(?:graphql\/query|api\/graphql|api\/v1\/(?:media|feed))(?:\/|$)/.test(url.pathname) &&
          response.status === 200 && /^(?:application\/(?:json|(?:x-)?javascript)|text\/javascript)(?:;|$)/i.test(response.mimeType) &&
          this.pending.size < 24)
        this.pending.add(params.requestId);
    } else if (method === 'Network.loadingFailed') this.pending.delete(params.requestId);
    else if (method === 'Network.loadingFinished' && this.pending.delete(params.requestId) &&
      Number.isFinite(params.encodedDataLength) && params.encodedDataLength <= MAX_BODY) {
      const generation = this.generation;
      const response = await this.web.debugger.sendCommand('Network.getResponseBody', { requestId: params.requestId });
      const body = response.base64Encoded ? Buffer.from(response.body, 'base64').toString('utf8') : response.body;
      if (typeof body !== 'string' || Buffer.byteLength(body) > MAX_BODY ||
          this.generation !== generation || this.web.isDestroyed()) return;
      // Instagram also labels JSON as JavaScript; parse data only, never execute it.
      // Cache only pruned public media, never the surrounding API/account response.
      this.remember(JSON.parse(body.replace(/^for \(;;\);/, '')));
    }
  }
  get(value) {
    const key = contentKey(value)?.match(/^instagram:(.+)$/)?.[1], row = this.cache.get(key);
    if (row && Date.now() - row.at > 300000) { this.cache.delete(key); return null; }
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
module.exports = { InstagramNetwork, publicContexts };
