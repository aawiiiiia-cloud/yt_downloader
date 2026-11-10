'use strict';
const { createHash } = require('node:crypto');
const path = require('node:path');
const { contentKey } = require('./model.cjs');
const identifier = value => typeof value === 'string' && /^[A-Za-z0-9_.+=-]{1,120}$/.test(value) ? value : null;
function sourceUrl(value) {
  const key = contentKey(value);
  if (!key) return null;
  const [site, id, extra] = key.split(':');
  if (!identifier(id)) return null;
  switch (site) {
    case 'youtube': return /^[\w-]{11}$/.test(id) ? `https://www.youtube.com/watch?v=${id}` : null;
    case 'instagram': return `https://www.instagram.com/p/${id}/`;
    case 'xiaohongshu': return /^[a-f0-9]{16,32}$/.test(id) ? `https://www.xiaohongshu.com/explore/${id}` : null;
    case 'bilibili': return /^(BV[\w]+|av\d+|ep\d+|ss\d+)$/.test(id) ?
      `https://www.bilibili.com/${/^(ep|ss)/.test(id) ? 'bangumi/play' : 'video'}/${id}?p=${/^\d{1,4}$/.test(extra || '') ? extra : 1}` : null;
    // Do not persist Vimeo access hashes or website signatures.
    case 'vimeo': return /^\d+$/.test(id) ? `https://vimeo.com/${id}` : null;
    case 'behance': return /^\d+$/.test(id) ? `https://www.behance.net/gallery/${id}` : null;
    case 'taobao': {
      const host = new URL(value).hostname;
      return /^\d+$/.test(id) ? `https://${host}/item.htm?id=${id}` : null;
    }
    case 'douyin': return /^\d+$/.test(id) ? `https://www.douyin.com/video/${id}` : null;
    case 'zcool': return `https://www.zcool.com.cn/work/${id}.html`;
    default: return null;
  }
}
function imageKeys(image = {}) {
  return [...new Set([image.url, ...(image.fallback_urls || [])].flatMap(value => {
    try {
      const url = new URL(value);
      if (!['http:', 'https:'].includes(url.protocol)) return [];
      const name = url.pathname.replace(/!.*/, '');
      return [createHash('sha256').update(name).digest('hex')];
    } catch { return []; }
  }))].slice(0, 12);
}
function quality(format) {
  return format ? { width: Number(format.width) || 0, height: Number(format.height) || 0,
    fps: Number(format.fps) || 0, ext: identifier(format.ext), audio: !!format.audio, video: !!format.video } : null;
}
function makeRecovery(snapshot, item, data, settings) {
  const url = sourceUrl(snapshot.url), itemUrl = sourceUrl(item.url);
  if (!url || !itemUrl) return null;
  return validateRecovery({
    version: 1, url, itemUrl, kind: item.kind, itemId: identifier(String(item.id || '')),
    mediaId: identifier(String(item.mediaId || '')), category: identifier(item.category),
    externalId: identifier(item.externalId),
    images: item.kind === 'images' ? [...new Set(data.indices)].map(index => {
      const image = item.images.find(row => row.index === index);
      return { index, keys: imageKeys(image) };
    }) : [],
    quality: quality(item.formats?.find(row => row.id === data.formatId)),
    settings: { saveDir: settings.saveDir, imageFormat: settings.imageFormat, imageQuality: settings.imageQuality,
      audioOnly: !!data.audioOnly, subtitles: !!data.subtitles },
  });
}
function validateRecovery(value) {
  if (!value || value.version !== 1 || !['images', 'video'].includes(value.kind)) return null;
  const url = sourceUrl(value.url), itemUrl = sourceUrl(value.itemUrl);
  if (!url || !itemUrl || typeof value.settings?.saveDir !== 'string' || !path.isAbsolute(value.settings.saveDir) ||
      !['original', 'jpg', 'png', 'webp'].includes(value.settings.imageFormat)) return null;
  const images = [];
  if (value.kind === 'images') {
    if (!Array.isArray(value.images) || !value.images.length || value.images.length > 2000 ||
        new Set(value.images.map(row => row?.index)).size !== value.images.length) return null;
    for (const image of value.images) {
      if (!image || !Number.isInteger(image.index) || image.index < 1 || image.index > 100000 ||
          !Array.isArray(image.keys) || !image.keys.length || image.keys.length > 12 ||
          image.keys.some(key => !/^[a-f0-9]{64}$/.test(key))) return null;
      images.push({ index: image.index, keys: [...new Set(image.keys)] });
    }
  }
  const q = value.quality;
  if (q && ['width', 'height', 'fps'].some(key => !Number.isFinite(q[key]) || q[key] < 0 || q[key] > 100000)) return null;
  return { version: 1, url, itemUrl, kind: value.kind, itemId: identifier(value.itemId),
    mediaId: identifier(value.mediaId), category: identifier(value.category), externalId: identifier(value.externalId),
    images, quality: q ? { width: q.width, height: q.height, fps: q.fps, ext: identifier(q.ext), audio: !!q.audio, video: !!q.video } : null,
    settings: { saveDir: value.settings.saveDir, imageFormat: value.settings.imageFormat,
      imageQuality: Number.isInteger(value.settings.imageQuality) && value.settings.imageQuality >= 1 && value.settings.imageQuality <= 100 ? value.settings.imageQuality : 95,
      audioOnly: !!value.settings.audioOnly, subtitles: !!value.settings.subtitles } };
}
function recoveredSelection(recovery, result) {
  const index = result.items.findIndex(item => item.kind === recovery.kind &&
    sourceUrl(item.url) === recovery.itemUrl &&
    (!recovery.itemId || String(item.id) === recovery.itemId) &&
    (!recovery.mediaId || String(item.mediaId) === recovery.mediaId) &&
    (!recovery.category || item.category === recovery.category) &&
    (!recovery.externalId || item.externalId === recovery.externalId));
  if (index < 0) throw new Error('\u539f\u7d20\u6750\u5df2\u53d8\u66f4\u6216\u4e0d\u53ef\u7528\uff0c\u8bf7\u624b\u52a8\u91cd\u65b0\u9009\u62e9');
  const item = result.items[index];
  for (const selected of recovery.images) {
    const image = item.images?.find(row => row.index === selected.index);
    if (!image || !imageKeys(image).some(key => selected.keys.includes(key)))
      throw new Error('\u56fe\u96c6\u5185\u5bb9\u6216\u987a\u5e8f\u5df2\u53d8\u66f4\uff0c\u8bf7\u624b\u52a8\u91cd\u65b0\u9009\u62e9');
  }
  let formatId = '';
  if (recovery.quality) {
    const q = recovery.quality;
    const format = item.formats?.find(row => JSON.stringify(quality(row)) === JSON.stringify(q));
    if (!format) throw new Error('\u539f\u753b\u8d28\u6682\u4e0d\u53ef\u7528\uff0c\u8bf7\u91cd\u65b0\u9009\u62e9\u753b\u8d28');
    formatId = format.id;
  }
  return { itemIndex: index, indices: recovery.images.map(row => row.index), formatId,
    audioOnly: recovery.settings.audioOnly, subtitles: recovery.settings.subtitles };
}
module.exports = { sourceUrl, imageKeys, makeRecovery, validateRecovery, recoveredSelection };
