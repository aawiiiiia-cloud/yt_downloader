'use strict';
const DOMAINS = {
  youtube: ['ytimg.com', 'ggpht.com', 'googleusercontent.com'],
  bilibili: ['hdslb.com', 'bilibili.com'],
  xiaohongshu: ['xhscdn.com', 'xiaohongshu.com', 'xhsimg.com'],
  vimeo: ['vimeocdn.com', 'vimeo.com'],
  douyin: ['douyinpic.com', 'douyincdn.com', 'byteimg.com', 'ibytedtos.com', 'pstatp.com'],
};
const REFERERS = { youtube: 'https://www.youtube.com/', bilibili: 'https://www.bilibili.com/', xiaohongshu: 'https://www.xiaohongshu.com/', vimeo: 'https://vimeo.com/', douyin: 'https://www.douyin.com/' };
function assetUrl(value, site) {
  const url = new URL(String(value).startsWith('//') ? 'https:' + value : value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || ![null, '', '80', '443'].includes(url.port) ||
      !DOMAINS[site]?.some(domain => url.hostname === domain || url.hostname.endsWith('.' + domain))) throw new Error('图片地址不属于当前站点的受支持素材域名');
  url.protocol = 'https:'; url.port = ''; url.hash = ''; return url.href;
}
async function readBody(response, maximum) {
  const reader = response.body?.getReader();
  if (!reader) throw new Error('没有取得图片内容');
  const buffers = []; let bytes = 0;
  try {
    while (true) {
      const chunk = await reader.read(); if (chunk.done) break;
      bytes += chunk.value.length;
      if (bytes > maximum) throw new Error('预览内容过大');
      buffers.push(Buffer.from(chunk.value));
    }
    return Buffer.concat(buffers);
  } finally { await reader.cancel().catch(() => {}); }
}
function imageMime(bytes) {
  if (bytes.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) return 'image/png';
  if (bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) return 'image/jpeg';
  if (/^GIF8[79]a/.test(bytes.subarray(0, 6).toString())) return 'image/gif';
  if (bytes.toString('ascii', 0, 4) === 'RIFF' && bytes.toString('ascii', 8, 12) === 'WEBP') return 'image/webp';
  if (bytes.toString('ascii', 4, 8) === 'ftyp' && /avif|avis/.test(bytes.toString('ascii', 8, 32))) return 'image/avif';
  throw new Error('服务器没有返回可显示的图片');
}
class Assets {
  constructor(fetch) { this.fetch = fetch; this.cache = new Map(); this.active = 0; this.queue = []; }
  async limit(work, signal) {
    if (signal?.aborted) throw new Error('页面已切换');
    if (this.active >= 4) await new Promise(resolve => this.queue.push(resolve));
    this.active++;
    try { if (signal?.aborted) throw new Error('页面已切换'); return await work(); }
    finally { this.active--; this.queue.shift()?.(); }
  }
  remember(key, value) {
    this.cache.delete(key); this.cache.set(key, { value, at: Date.now() });
    while (this.cache.size > 48 || [...this.cache.values()].reduce((sum, row) => sum + (row.value.dataUrl?.length || 0), 0) > 24000000) this.cache.delete(this.cache.keys().next().value);
    return value;
  }
  async request(url, site, options, signal) {
    for (let redirect = 0; redirect < 5; redirect++) {
      url = assetUrl(url, site);
      const response = await this.fetch(url, { ...options, redirect: 'manual', credentials: 'include', signal,
        headers: { Referer: REFERERS[site], ...options.headers } });
      if ([301,302,303,307,308].includes(response.status)) {
        const next = response.headers.get('location'); await response.body?.cancel();
        if (!next) throw new Error('素材跳转地址为空');
        url = new URL(next, url).href; continue;
      }
      return response;
    }
    throw new Error('素材地址跳转过多');
  }
  preview(value, site, signal, refresh = false) {
    const url = assetUrl(value, site), key = 'preview:' + url;
    if (!refresh && this.cache.has(key)) return Promise.resolve(this.cache.get(key).value);
    return this.limit(async () => {
      const timer = AbortSignal.timeout(12000), abort = signal ? AbortSignal.any([signal, timer]) : timer;
      const response = await this.request(url, site, { cache: refresh ? 'reload' : 'default' }, abort);
      if (!response.ok) { await response.body?.cancel(); throw new Error(`预览加载失败（${response.status}），可点击重试`); }
      const bytes = await readBody(response, 6 * 1024 * 1024), mime = imageMime(bytes);
      return this.remember(key, { dataUrl: `data:${mime};base64,${bytes.toString('base64')}` });
    }, signal);
  }
  sourceSize(values, site, signal) {
    const urls = [...new Set(values.map(value => assetUrl(value, site)))], key = 'size:' + urls.join('|');
    const cached = this.cache.get(key);
    if (cached && Date.now() - cached.at < 300000) return Promise.resolve(cached.value);
    return this.limit(async () => {
      const timer = AbortSignal.timeout(10000), abort = signal ? AbortSignal.any([signal, timer]) : timer;
      for (const url of urls) {
        for (const method of ['HEAD', 'GET']) {
          try {
            const response = await this.request(url, site, { method, headers: method === 'GET' ? { Range: 'bytes=0-0' } : {} }, abort);
            const type = response.headers.get('content-type') || '';
            const range = response.headers.get('content-range')?.match(/^bytes\s+\d+-\d+\/(\d+)$/i);
            const size = Number(range?.[1] || (response.status === 200 ? response.headers.get('content-length') : 0));
            const encoding = response.headers.get('content-encoding');
            await response.body?.cancel();
            if (response.ok && /^(image\/|application\/octet-stream)/i.test(type) && (!encoding || encoding === 'identity') && size > 0)
              return this.remember(key, { size, displayFallback: urls.length > 1 && url === urls.at(-1) });
          } catch (error) { if (abort.aborted) throw error; }
        }
      }
      return this.remember(key, { size: null });
    }, signal);
  }
}
module.exports = { Assets, assetUrl, imageMime, readBody };
