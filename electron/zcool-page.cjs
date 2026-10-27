'use strict';
// Serialize only this work's public media fields; never forward the full Next.js store.
function workSnapshot(root, value) {
  let url, id;
  try {
    url = new URL(value);
    id = decodeURIComponent(url.pathname).match(/^\/work\/(Z[A-Za-z0-9_+=-]{5,100})\.html$/)?.[1];
  } catch { return null; }
  if (!['https:', 'http:'].includes(url.protocol) || !['www.zcool.com.cn', 'zcool.com.cn'].includes(url.hostname) || !id) return null;
  const props = root?.props?.pageProps, data = props?.data;
  const stored = String(root?.query?.id || '').replace(/\.html$/, '');
  if (!data || stored !== id) return { workId: id, mismatch: true, images: [] };
  if ((data.productImages != null && !Array.isArray(data.productImages)) || (data.productImages?.length || 0) > 400) return null;
  const fields = ['id', 'url', 'urlBig', 'urlSmall', 'width', 'height', 'orderNo'];
  const images = (data.productImages || []).map(row => Object.fromEntries(fields
    .filter(key => ['string', 'number'].includes(typeof row?.[key])).map(key => [key, row[key]])));
  const originalVideos = Array.isArray(data.productVideos) ? data.productVideos : [];
  const videos = originalVideos.slice(0, 24).flatMap(row => {
    try {
      const target = new URL(row?.url);
      if (!['http:', 'https:'].includes(target.protocol) || target.hostname !== 'video.zcool.cn' ||
          target.username || target.password || !['', '80', '443'].includes(target.port) || !target.pathname.toLowerCase().endsWith('.mp4')) return [];
      target.protocol = 'https:'; target.port = ''; target.hash = '';
      const value = { url: target.href };
      for (const key of ['id', 'type', 'width', 'height']) if (['string', 'number'].includes(typeof row[key])) value[key] = row[key];
      if (typeof row.cover === 'string') {
        try {
          const cover = new URL(row.cover);
          if (cover.protocol === 'https:' && !cover.username && !cover.password && !cover.port &&
              ['zcool.cn', 'zcool.com.cn'].some(host => cover.hostname === host || cover.hostname.endsWith('.' + host))) value.cover = cover.href;
        } catch { /* A missing cover must not discard a playable native video. */ }
      }
      return [value];
    } catch { return []; }
  });
  const result = { workId: id, title: String(data.title || '').slice(0, 240),
    author: String(data.creatorObj?.username || '').slice(0, 120), images, videos,
    skippedVideos: originalVideos.length - videos.length };
  return JSON.stringify(result).length <= 1200000 ? result : null;
}
const snapshotScript = `(() => {
  const element = document.getElementById('__NEXT_DATA__');
  if (!element || element.textContent.length > 2500000) return null;
  let root; try { root = JSON.parse(element.textContent); } catch { return null; }
  return (${workSnapshot.toString()})(root, location.href);
})()`;
module.exports = { workSnapshot, snapshotScript };
