'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { app, nativeImage } = require('electron');
const id = '1078472227366';
const prefix = 'https://img.alicdn.com/collector-preview/';
const originals = ['main1.jpg', 'main2.jpg', 'main3.png', 'main4.png'].map(name => prefix + name);
const oversizedLength = 7 * 1024 * 1024;

function fixture(request, imageBytes) {
  const url = new URL(request.url);
  if (url.hostname === 'item.taobao.com' && url.searchParams.get('id') === id) {
    const data = { loaderData: { home: { data: { item: { itemId: id, title: 'Preview fixture', images: originals } } } } };
    const previews = [originals[0] + '_800x800q90.jpg', originals[1], originals[2] + '_q50.jpg_.webp', originals[3]];
    return new Response(`<html><head><title>Preview fixture</title></head><body><h1>Preview fixture</h1>
      <script>window.__ICE_APP_CONTEXT__=${JSON.stringify(data)}</script>
      <div class="PicGallery--fixture">${previews.map(src => `<img src="${src}">`).join('')}</div></body></html>`,
    { headers: { 'content-type': 'text/html; charset=utf-8' } });
  }
  if (!request.url.startsWith(prefix)) return null;
  const oversized = request.url === originals[3];
  const bytes = oversized ? Buffer.concat([imageBytes, Buffer.alloc(oversizedLength - imageBytes.length)]) :
    nativeImage.createFromBuffer(imageBytes).resize({ width: 800, height: 600 }).toPNG();
  return new Response(request.method === 'HEAD' ? null : bytes,
    { headers: { 'content-type': 'image/png', 'content-length': String(bytes.length) } });
}

async function run(collector, check, until, wait, assetRequests) {
  const source = collector.tabs.active;
  const add = collector.queue.add;
  const ui = code => collector.window.webContents.executeJavaScript(code);
  let spec;
  try {
    const tab = collector.openTab(`https://item.taobao.com/item.htm?id=${id}&skuId=6313661907925`, { group: 'taobao' });
    await until(() => tab.state.site === 'taobao' && !tab.view.webContents.isLoadingMainFrame(), 'preview fixture did not load');
    await collector.inspect();
    const item = collector.state.result?.items[0];
    check('four-image product fixture parses through the real Python adapter', item?.images.length === 4);
    check('quality-only DOM preview replaces the JSON original', item.images[2].url === originals[2] &&
      item.images[2].preview_url === originals[2] + '_q50.jpg_.webp');
    check('oversized fourth image retains its original download URL', item.images[3].url === originals[3] &&
      item.images[3].preview_url === originals[3]);
    await ui("document.getElementById('resources-tab').click()");
    await until(() => ui("document.querySelector('[data-preview=\"4\"]') !== null"), 'fourth preview frame did not render');
    await ui("document.querySelector('[data-preview=\"4\"]').scrollIntoView({block:'center'})");
    await until(() => ui("document.querySelector('[data-preview=\"4\"] img')?.naturalWidth === 800"), 'oversized image did not use a decodable fallback');
    await until(() => ui("document.querySelector('[data-preview=\"4\"]').closest('.image-card').querySelector('[data-image-size]').textContent.includes('7.34MB')"), 'original size was replaced by thumbnail bytes');
    check('fourth preview displays resized pixels and hides the size-limit error', await ui("!document.querySelector('[data-preview=\"4\"] img').hidden && document.querySelector('[data-preview=\"4\"] .preview-message').hidden"));
    check('fourth image labels retain source dimensions, not preview dimensions', await ui("document.querySelector('[data-preview=\"4\"]').closest('.image-card').querySelector('[data-image-dimensions]').textContent.includes('4\\u00d73')"));
    check('CDN fallback stays in the source host referrer policy', assetRequests.some(row => row.url === originals[3] + '_800x800q75.jpg' && row.referer === 'https://item.taobao.com/'));
    const metadata = await collector.asset({ inspectionId: collector.state.inspectionId, itemIndex: 0, imageIndex: 4 }, 'size');
    check('cached thumbnail never replaces original metadata', metadata.size === oversizedLength && metadata.width === 4 && metadata.height === 3 && !metadata.displayFallback);
    collector.queue.add = value => { spec = value; return 'preview-fixture-task'; };
    await ui("document.querySelector('[data-download-image=\"4\"]').click()");
    await until(() => spec?.indices[0] === 4, 'fourth image download did not reach the queue');
    check('fourth download still freezes the original product and index', spec.mediaId === 'main' && spec.context.itemId === id &&
      spec.context.groups.main[3].url === originals[3]);
    await until(() => ui("!document.querySelector('[data-download-image=\"4\"]').disabled"), 'fourth download button remained busy');
    const bounds = await ui("(()=>{const r=document.querySelector('aside').getBoundingClientRect();return{x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)}})()");
    await wait(120);
    const screenshot = await collector.window.webContents.capturePage(bounds);
    check('preview panel has a nonempty rendered screenshot', !screenshot.isEmpty());
    fs.writeFileSync(path.join(app.getPath('userData'), '..', 'taobao-preview-panel.png'), screenshot.toPNG());
  } finally { collector.queue.add = add; collector.activateTab(source.id); }
}
module.exports = { fixture, run };
