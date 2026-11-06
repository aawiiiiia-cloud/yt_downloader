'use strict';

function postSnapshot(document, value, isComplete = completeContext) {
  const url = new URL(value);
  const code = url.pathname.match(/^\/(?:(?!accounts\/|stories\/|explore\/|direct\/|share\/|challenge\/|checkpoint\/)[A-Za-z0-9._]{1,30}\/)?(?:p|reels?)\/([A-Za-z0-9_-]{5,32})\/?$/)?.[1];
  if (!code || !['www.instagram.com', 'instagram.com'].includes(url.hostname) ||
      !['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
      !['', '80', '443'].includes(url.port)) return null;
  let pk = 0n;
  for (const char of code) pk = pk * 64n + BigInt('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'.indexOf(char));
  const expected = String(pk);
  const stack = [];
  let bytes = 0, visited = 0, best = null, bestScore = -1;
  for (const script of document.querySelectorAll('script[data-sjs], script[type="application/json"]')) {
    const text = script.textContent || '';
    bytes += text.length;
    if (bytes > 8 * 1024 * 1024) break;
    try { stack.push(JSON.parse(text)); } catch {}
  }
  const id = item => String(item?.pk || item?.id || '').split('_')[0];
  const area = row => Number(row?.width || 0) * Number(row?.height || 0);
  const candidates = rows => (Array.isArray(rows) ? [...rows] : [])
    .sort((a, b) => area(b) - area(a)).slice(0, 32).map(row => ({
      url: row.url, width: row.width, height: row.height, type: row.type,
      file_size: ['number', 'string'].includes(typeof row.file_size) ? row.file_size : undefined,
      content_length: ['number', 'string'].includes(typeof row.content_length) ? row.content_length : undefined,
    }));
  const media = item => ({
    pk: id(item), code: item.code, media_type: item.media_type,
    taken_at: item.taken_at, original_width: item.original_width, original_height: item.original_height,
    image_versions2: { candidates: candidates(item.image_versions2?.candidates) },
    video_versions: candidates(item.video_versions), video_dash_manifest: item.video_dash_manifest,
    video_duration: item.video_duration,
  });
  while (stack.length && ++visited <= 200000) {
    const item = stack.pop();
    if (!item || typeof item !== 'object') continue;
    if (!Array.isArray(item) && item.code === code && id(item) === expected &&
        (item.image_versions2 || item.carousel_media)) {
      // Duplicate payloads may have equal child counts but missing video URLs.
      const post = { ...media(item), code, pk: expected,
        user: { pk: id(item.user), username: item.user?.username, full_name: item.user?.full_name },
        caption: item.caption?.text ? { text: String(item.caption.text).slice(0, 20000) } : null,
        carousel_media_count: item.carousel_media_count,
        carousel_media: Array.isArray(item.carousel_media) ? item.carousel_media.slice(0, 101).map(media) : undefined,
      };
      const context = { instagram: { shortcode: code, post } };
      const rows = post.carousel_media || [post];
      const score = (isComplete(context) ? 1000000 : 0) + rows.length * 1000 +
        rows.reduce((sum, row) => sum + row.image_versions2.candidates.length + row.video_versions.length +
          (row.video_dash_manifest ? 40 : 0) + (Number(row.video_duration) > 0 ? 10 : 0), 0);
      if (score > bestScore) { best = context; bestScore = score; }
    }
    for (const child of Object.values(item)) {
      if (child && typeof child === 'object' && stack.length + visited < 200000) stack.push(child);
    }
  }
  if (!best) return null;
  // Only public post media and author/caption cross into the task worker.
  const encoded = JSON.stringify(best);
  return encoded.length <= 1400000 ? JSON.parse(encoded) : null;
}

const snapshotScript = `(${postSnapshot.toString()})(document, location.href, ${completeContext.toString()})`;
function htmlSnapshotScript(html, url) {
  return `(${postSnapshot.toString()})(new DOMParser().parseFromString(${JSON.stringify(html)}, 'text/html'), ${JSON.stringify(url)}, ${completeContext.toString()})`;
}
function completeContext(context) {
  const post = context?.instagram?.post, rows = post?.carousel_media || (post ? [post] : []);
  return Array.isArray(rows) && rows.length > 0 && rows.length <= 100 &&
    (post.carousel_media_count == null || post.carousel_media_count === rows.length) &&
    new Set(rows.map(row => row?.pk)).size === rows.length &&
    rows.every(row => /^\d{1,30}$/.test(row?.pk || '') && row.image_versions2?.candidates?.length &&
      (row.media_type !== 2 || row.video_versions?.length));
}
module.exports = { postSnapshot, snapshotScript, htmlSnapshotScript, completeContext };
