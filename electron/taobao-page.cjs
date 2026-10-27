'use strict';

// Independent media-only adapter. Never serializes the site's account/session store.
function productMedia(root, wanted) {
  const found = { main: [], sku: [], detail: [], title: '', matched: false, other: false };
  let budget = 5000;
  const list = value => Array.isArray(value) ? value : [];
  const add = (category, value, name = '') => {
    if (found[category].length >= 400) return;
    const url = typeof value === 'string' ? value : value?.url || value?.image || value?.imageUrl;
    if (typeof url !== 'string' || url.length > 4000) return;
    found[category].push({ url, name: String(name || value?.name || '').slice(0, 160), width: value?.width, height: value?.height });
  };
  const walk = (value, depth = 0) => {
    if (!value || typeof value !== 'object' || depth > 7 || --budget < 0) return;
    const item = value.item || value.itemInfo || value;
    const id = String(item.itemId || item.itemNumId || '');
    if (/^\d+$/.test(id)) {
      if (id !== wanted) { found.other = true; return; }
      found.matched = true; found.title ||= String(item.title || '').slice(0, 240);
      for (const image of (Array.isArray(item.images) ? item.images : []).slice(0, 200)) add('main', image);
      for (const prop of list(value.skuBase?.props || item.skuBase?.props).slice(0, 60)) {
        for (const option of list(prop?.values).slice(0, 400)) {
          if (!option || typeof option !== 'object') continue;
          if (option.image || option.imageUrl) add('sku', option.image || option.imageUrl, [prop.name, option.name].filter(Boolean).join('：'));
        }
      }
      // Explicit image lists only; never scan arbitrary JSON strings for CDN URLs.
      for (const image of list(value.detailImages || item.detailImages).slice(0, 400)) add('detail', image);
      return;
    }
    for (const [, child] of Object.entries(value).filter(([key]) => !/recommend|related|review|comment|user|login|token|account/i.test(key)).slice(0, 100)) {
      if (Array.isArray(child)) for (const row of child.slice(0, 400)) walk(row, depth + 1);
      else walk(child, depth + 1);
    }
  };
  walk(root); return found;
}

function readPage() {
  const page = new URL(location.href), id = page.searchParams.get('id');
  if (!['item.taobao.com', 'detail.tmall.com', 'chaoshi.detail.tmall.com'].includes(page.hostname) ||
      page.pathname !== '/item.htm' || !/^\d{1,30}$/.test(id || '')) return null;
  const selectors = {
    main: '[class*="PicGallery"], [class*="picGallery"], [class*="mainPic"], [class*="MainPic"], #J_UlThumb, #J_ImgBooth, .tb-gallery, .tb-main-pic, .tb-booth, [data-product-gallery]',
    sku: '[class*="skuItem"], [class*="SkuItem"], [class*="skuValue"], .tb-sku, #J_SKU, [data-sku-group]',
    detail: '#imageTextInfo-content, #J_DivItemDesc, .desc-root, .descV8-container, .tb-desc-content, #description, [data-product-description]',
  };
  const blocked = '[class*="recommend"], [class*="Recommend"], [class*="comment"], [class*="Comment"], [class*="review"], [class*="Review"], .tb-shop, [data-recommendation]';
  const groups = { main: [], sku: [], detail: [] };
  let detailPresent = false, title = '', matched = false, other = false;
  const metadata = [window.__ICE_APP_CONTEXT__, window.__INITIAL_STATE__, window.__INIT_DATA__, window.g_config];
  for (const element of [...document.querySelectorAll('script[type="application/json"]')].slice(0, 8)) {
    if (element.textContent.length > 1000000) continue;
    try { metadata.push(JSON.parse(element.textContent)); } catch {}
  }
  for (const data of metadata) {
    const value = productMedia(data, id);
    matched ||= value.matched; other ||= value.other; title ||= value.title;
    for (const key of Object.keys(groups)) groups[key].push(...value[key]);
  }
  if (!matched && other) return { itemId: id, mismatch: true, groups: { main: [], sku: [], detail: [] } };
  const image = (node, category) => {
    if (node.closest(blocked)) return;
    const sources = ['data-src', 'data-ks-lazyload', 'data-original', 'data-lazy-src', 'data-src-full', 'src'].map(key => node.getAttribute(key)).filter(Boolean);
    const set = node.getAttribute('data-srcset') || node.getAttribute('srcset');
    if (set) sources.unshift(...set.split(',').map(row => row.trim().split(/\s+/)[0]).reverse());
    if (node.currentSrc) sources.push(node.currentSrc);
    const owner = node.closest('[data-vid], [data-value], [data-sku-id], [class*="skuItem"], [class*="skuValue"]');
    const label = owner?.getAttribute('title') || owner?.querySelector('[title]')?.getAttribute('title') || owner?.textContent?.trim() || node.alt || '';
    const width = Number(node.getAttribute('data-original-width')) || Number(node.getAttribute('width')) || null;
    const height = Number(node.getAttribute('data-original-height')) || Number(node.getAttribute('height')) || null;
    if (sources.length && groups[category].length < 400) groups[category].push({ url: sources[0], alternatives: sources.slice(1, 5),
      previewUrl: node.currentSrc || node.getAttribute('src') || '', name: category === 'sku' ? label.slice(0, 160) : '',
      // DOM/layout or thumbnail dimensions must not be advertised as original dimensions.
      width: node.hasAttribute('data-original-width') ? width : null, height: node.hasAttribute('data-original-height') ? height : null });
  };
  for (const [category, selector] of Object.entries(selectors)) {
    for (const container of [...document.querySelectorAll(selector)].slice(0, 80)) {
      if (container.closest(blocked)) continue;
      if (category === 'detail') detailPresent = true;
      if (container.tagName === 'IMG') image(container, category);
      else for (const node of [...container.querySelectorAll('img')].slice(0, 400)) image(node, category);
      if (category === 'sku') for (const node of [...container.querySelectorAll('[style*="background"], [data-value]')].slice(0, 400)) {
        const url = (node.style?.backgroundImage || '').match(/url\(["']?([^"')]+)["']?\)/)?.[1];
        if (url && !node.closest(blocked) && groups.sku.length < 400) groups.sku.push({ url, name: (node.getAttribute('title') || node.textContent || '').trim().slice(0, 160) });
      }
    }
  }
  title ||= document.querySelector('[class*="ItemHeader"] h1, [class*="ItemTitle"], #J_Title h3, .tb-main-title, h1')?.textContent?.trim() || document.title;
  const result = { itemId: id, skuId: page.searchParams.get('skuId') || '', title: String(title).slice(0, 240), groups, detailPresent };
  for (const key of Object.keys(groups)) {
    const seen = new Set(); groups[key] = groups[key].filter(row => { const k = row.url + '\n' + row.name; if (seen.has(k)) return false; seen.add(k); return true; }).slice(0, 400);
  }
  if (JSON.stringify(result).length > 1200000) throw new Error('商品图片信息过多，请缩小采集范围');
  return result;
}

const snapshotScript = `(() => { const productMedia = ${productMedia.toString()}; return (${readPage.toString()})(); })()`;
module.exports = { productMedia, snapshotScript };
