'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { app } = require('electron');
const { snapshotScript } = require('./zcool-page.cjs');
const URL = 'https://www.zcool.com.cn/work/ZNzQyNjY2ODg=.html';
function fixture() {
  const images = Array.from({ length: 28 }, (_, index) => ({ id: `image-${index + 1}`,
    urlBig: `https://img.zcool.cn/community/${index + 1}.jpg?x-oss-process=image/resize,w_2&k=fixture&t=fixture`,
    urlSmall: `https://img.zcool.cn/community/${index + 1}.jpg?x-oss-process=image/resize,w_1&k=fixture&t=fixture`,
    width: 12, height: 9, proofToken: 'do-not-forward-proof' }));
  const root = { query: { id: 'ZNzQyNjY2ODg=.html' }, props: { pageProps: { data: {
    title: '受控站酷作品 · 28 张', creatorObj: { username: '测试作者', token: 'do-not-forward-account' },
    productImages: images, recommendWorks: [{ url: 'https://img.zcool.cn/recommend.jpg' }],
  } } } };
  return `<html><head><title>受控站酷作品</title></head><body><h1>受控站酷作品</h1><script id="__NEXT_DATA__" type="application/json">${JSON.stringify(root)}</script></body></html>`;
}
async function run(collector, check, until, wait, assetRequests) {
  const source = collector.tabs.active, add = collector.queue.add;
  const ui = code => collector.window.webContents.executeJavaScript(code);
  let spec, title;
  try {
    const tab = collector.openTab(URL, { group: 'zcool' });
    await until(() => tab.state.site === 'zcool' && !tab.view.webContents.isLoadingMainFrame(), 'ZCOOL fixture did not load');
    await ui("document.getElementById('resources-tab').click()");
    await collector.inspect();
    check('ZCOOL page parses exactly 28 ordered body images', collector.state.result?.items[0].images.length === 28 &&
      collector.state.result.items[0].images[27].index === 28);
    check('ZCOOL snapshots exclude private stores and recommendations', !/do-not-forward|recommend.jpg/.test(JSON.stringify(collector.publicState())));
    await until(() => ui("document.querySelector('[data-preview-image]')?.naturalWidth>0 && document.querySelector('.image-meta')?.textContent.includes('4 × 3')"), 'ZCOOL preview or actual dimensions missing');
    check('ZCOOL size cards never mislabel resized files as originals', await ui("document.querySelector('[data-image-size]').textContent.startsWith('图片 ') && !document.querySelector('.image-meta').textContent.includes('12 × 9')"));
    check('ZCOOL explains the original-size limitation and exposes only web login and cookie import', await ui("document.querySelector('.collection-notice').textContent.includes('缩放图片') && !document.getElementById('web-login').hidden && document.getElementById('edge-login').hidden && !document.getElementById('import-login').hidden"));
    check('ZCOOL asset requests retain complete signed parameters and permitted origin', assetRequests.some(row =>
      row.url.includes('img.zcool.cn') && row.url.includes('k=fixture&t=fixture') && row.referer === 'https://www.zcool.com.cn/'));
    collector.queue.add = (value, label) => { spec = value; title = label; return 'zcool-isolated-task'; };
    await ui("document.querySelector('[data-image=\"1\"]').checked=false;document.querySelector('[data-download-image=\"2\"]').click()");
    await until(() => spec?.indices?.length === 1 && spec.indices[0] === 2, 'ZCOOL single download did not queue');
    check('ZCOOL individual downloads freeze the original work snapshot and sequence', spec.context.workId === 'ZNzQyNjY2ODg=' && spec.mediaId === 'ZNzQyNjY2ODg=' && title.endsWith('图片 02'));
    await until(() => ui("!document.querySelector('[data-download-image=\"2\"]').disabled"), 'ZCOOL single button stayed busy');
    await ui("document.getElementById('download').click()");
    await until(() => spec?.indices?.length === 27, 'ZCOOL selected batch did not queue');
    check('ZCOOL selected batch honors unchecked images and stable content URLs', !spec.indices.includes(1) && spec.url === URL);
    await ui("document.getElementById('resources-tab').click();document.getElementById('resources-panel').scrollTop=0");
    await wait(100);
    fs.writeFileSync(path.join(app.getPath('userData'), '..', 'zcool-panel.png'), (await collector.window.webContents.capturePage()).toPNG());
    const receipt = collector.state.inspectionId;
    await tab.view.webContents.mainFrame.executeJavaScript("history.pushState({},'', '/work/ZanotherWork.html')");
    await wait(120);
    check('ZCOOL manual mode retains its completed inspection across navigation', collector.state.inspectionId === receipt);
    const stale = await collector.readPage(snapshotScript);
    check('ZCOOL stale page JSON cannot be attached to a different work', stale.mismatch === true && stale.images.length === 0);
  } finally { collector.queue.add = add; collector.activateTab(source.id); }
}
module.exports = { fixture, run };
