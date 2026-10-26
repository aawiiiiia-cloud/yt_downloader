'use strict';
const DOMAINS = {
  youtube: ['ytimg.com', 'ggpht.com', 'googleusercontent.com'],
  bilibili: ['hdslb.com', 'bilibili.com'],
  xiaohongshu: ['xhscdn.com', 'xiaohongshu.com', 'xhsimg.com'],
  vimeo: ['vimeocdn.com', 'vimeo.com'],
  douyin: ['douyinpic.com', 'douyincdn.com', 'byteimg.com', 'ibytedtos.com', 'pstatp.com'],
  behance: ['behance.net', 'vimeocdn.com', 'adobe.com', 'adobe.net'],
  taobao: ['alicdn.com', 'tbcdn.cn', 'taobaocdn.com'],
  zcool: ['zcool.cn', 'zcool.com.cn'],
};
const REFERERS = { youtube: 'https://www.youtube.com/', bilibili: 'https://www.bilibili.com/', xiaohongshu: 'https://www.xiaohongshu.com/', vimeo: 'https://vimeo.com/', douyin: 'https://www.douyin.com/', behance: 'https://www.behance.net/', taobao: 'https://item.taobao.com/', zcool: 'https://www.zcool.com.cn/' };
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
function referrerOrigin(value) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('图片来源地址无效');
  // Chromium validates Referer against its default cross-origin policy. A full
  // product path/query is rejected before I/O; send the permitted origin only.
  return url.origin + '/';
}
function imageDimensions(bytes) {
  const result = (width, height) => width > 0 && height > 0 && width < 1000000 && height < 1000000 ? { width, height } : null;
  if (bytes.length >= 24 && bytes.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10])) && bytes.toString('ascii',12,16) === 'IHDR')
    return result(bytes.readUInt32BE(16),bytes.readUInt32BE(20));
  if (bytes.length >= 10 && /^GIF8[79]a/.test(bytes.toString('ascii',0,6))) return result(bytes.readUInt16LE(6),bytes.readUInt16LE(8));
  if (bytes.length >= 25 && bytes.toString('ascii',0,4) === 'RIFF' && bytes.toString('ascii',8,12) === 'WEBP') {
    const kind=bytes.toString('ascii',12,16);
    if (kind==='VP8X' && bytes.length>=30) return result(bytes.readUIntLE(24,3)+1,bytes.readUIntLE(27,3)+1);
    if (kind==='VP8L' && bytes[20]===0x2f) return result(1+bytes[21]+((bytes[22]&0x3f)<<8),1+(bytes[22]>>6)+(bytes[23]<<2)+((bytes[24]&15)<<10));
    if (kind==='VP8 ' && bytes.length>=30 && bytes.subarray(23,26).equals(Buffer.from([0x9d,1,0x2a]))) return result(bytes.readUInt16LE(26)&0x3fff,bytes.readUInt16LE(28)&0x3fff);
  }
  if (bytes.length >= 4 && bytes[0]===0xff && bytes[1]===0xd8) {
    let offset=2;
    while(offset+4<=bytes.length) {
      if(bytes[offset]!==0xff)return null;
      while(bytes[offset]===0xff)offset++;
      const marker=bytes[offset++];
      if(marker===0xda || marker===0xd9)return null;
      if(marker===0x01 || (marker>=0xd0 && marker<=0xd8))continue;
      if(offset+2>bytes.length)return null;
      const length=bytes.readUInt16BE(offset);
      if(length<2 || offset+length>bytes.length)return null;
      if([0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf].includes(marker) && length>=8)
        return result(bytes.readUInt16BE(offset+5),bytes.readUInt16BE(offset+3));
      offset+=length;
    }
  }
  return null;
}
async function readDimensions(response, maximum = 256 * 1024) {
  const reader=response.body?.getReader();if(!reader)return {};
  const chunks=[];let length=0;
  try {
    while(length<maximum) {
      const {value,done}=await reader.read();if(done)break;
      const chunk=Buffer.from(value).subarray(0,maximum-length);chunks.push(chunk);length+=chunk.length;
      const dimensions=imageDimensions(Buffer.concat(chunks,length));
      if(dimensions)return dimensions;
    }
    return {};
  } finally {await reader.cancel().catch(()=>{});}
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
        headers: { ...options.headers, Referer: referrerOrigin(options.headers?.Referer || REFERERS[site]) } });
      if ([301,302,303,307,308].includes(response.status)) {
        const next = response.headers.get('location'); await response.body?.cancel();
        if (!next) throw new Error('素材跳转地址为空');
        url = new URL(next, url).href; continue;
      }
      return response;
    }
    throw new Error('素材地址跳转过多');
  }
  preview(value, site, signal, refresh = false, referrer) {
    const url = assetUrl(value, site), key = 'preview:' + url + (referrer ? '|' + referrer : '');
    if (!refresh && this.cache.has(key)) return Promise.resolve(this.cache.get(key).value);
    return this.limit(async () => {
      const timer = AbortSignal.timeout(12000), abort = signal ? AbortSignal.any([signal, timer]) : timer;
      const response = await this.request(url, site, { cache: refresh ? 'reload' : 'default', ...(referrer ? { headers: { Referer: referrer } } : {}) }, abort);
      if (!response.ok) { await response.body?.cancel(); throw new Error(`预览加载失败（${response.status}），可点击重试`); }
      const bytes = await readBody(response, 6 * 1024 * 1024), mime = imageMime(bytes);
      return this.remember(key, { dataUrl: `data:${mime};base64,${bytes.toString('base64')}` });
    }, signal);
  }
  sourceSize(values, site, signal, referrer, needDimensions = false) {
    const urls = [...new Set(values.map(value => assetUrl(value, site)))], key = 'size:' + urls.join('|') + (referrer ? '|' + referrer : '') + (needDimensions ? '|dimensions' : '');
    const cached = this.cache.get(key);
    if (cached && Date.now() - cached.at < 300000) return Promise.resolve(cached.value);
    return this.limit(async () => {
      const timer = AbortSignal.timeout(10000), abort = signal ? AbortSignal.any([signal, timer]) : timer;
      for (const url of urls) {
        const dimensionsWanted=needDimensions && url===urls[0];let knownSize=null;
        for (const method of ['HEAD', 'GET']) {
          try {
            const response = await this.request(url, site, { method, headers: { ...(method === 'GET' ? { Range: dimensionsWanted ? 'bytes=0-262143' : 'bytes=0-0' } : {}), ...(referrer ? { Referer: referrer } : {}) } }, abort);
            const type = response.headers.get('content-type') || '';
            const range = response.headers.get('content-range')?.match(/^bytes\s+\d+-\d+\/(\d+)$/i);
            const size = Number(range?.[1] || (response.status === 200 ? response.headers.get('content-length') : 0));
            const encoding = response.headers.get('content-encoding');
            const valid=response.ok && /^(image\/|application\/octet-stream)/i.test(type) && (!encoding || encoding === 'identity');
            if(valid && size>0)knownSize=size;
            const dimensions=valid && dimensionsWanted && method==='GET' ? await readDimensions(response) : (await response.body?.cancel(),{});
            if(valid && ((knownSize && (!dimensionsWanted || method==='GET')) || dimensions.width))
              return this.remember(key, { size:knownSize, ...dimensions, displayFallback: urls.length > 1 && url === urls.at(-1) });
          } catch (error) {
            if (abort.aborted) {
              if (knownSize && !signal?.aborted) return this.remember(key,{size:knownSize,displayFallback:urls.length>1 && url===urls.at(-1)});
              throw error;
            }
          }
        }
        if(knownSize)return this.remember(key,{size:knownSize,displayFallback:urls.length>1 && url===urls.at(-1)});
      }
      return this.remember(key, { size: null });
    }, signal);
  }
}
module.exports = { Assets, assetUrl, imageMime, readBody, referrerOrigin, imageDimensions, readDimensions };
