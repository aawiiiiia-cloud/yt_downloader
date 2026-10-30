'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { app } = require('electron');
const { snapshotScript } = require('./zcool-page.cjs');
const { failureError } = require('./task-failure.cjs');
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
async function loginChecks(collector, check, until) {
  const ui = code => collector.window.webContents.executeJavaScript(code);
  const receipt = collector.state.inspectionId;
  await ui("document.querySelector('[data-image=\"1\"]').checked=false");
  const set = (name, value) => collector.browserSession.cookies.set({ url: 'https://www.zcool.com.cn/', domain: '.zcool.com.cn',
    path: '/', name, value, secure: true, httpOnly: true, expirationDate: Date.now() / 1000 + 3600 });
  const signedIn = () => ui("document.getElementById('login-state').textContent.includes('【已检测到登录凭据】如左侧网页显示未登录，请重新登录') && !document.getElementById('login-state').classList.contains('login-warning') && !document.getElementById('login-state').classList.contains('login-success')");
  const signedOut = () => ui("document.getElementById('login-state').textContent.includes('【未检测到登录凭据】建议登录') && document.getElementById('login-state').classList.contains('login-warning')");
  const expired = () => ui("document.getElementById('login-state').textContent.includes('【已检测到登录凭据】如左侧网页显示未登录，请重新登录') && document.getElementById('login-state').classList.contains('login-warning')");
  await set('JSESSIONID', 'zcool-fixture-visitor'); await collector.updateLogin();
  await until(signedOut, 'anonymous ZCOOL cookies incorrectly logged in');
  check('ZCOOL anonymous sessions show a red sign-in suggestion', !collector.state.login.zcool);
  await set('zcool_logon_new', 'zcool-fixture-logon'); await collector.updateLogin();
  await until(signedOut, 'a partial ZCOOL credential pair incorrectly logged in');
  check('ZCOOL a partial credential pair remains signed out', !collector.state.login.zcool);
  await set('zcool_mt', 'zcool-fixture-token');
  await until(signedIn, 'ZCOOL cookie change did not update the account header');
  check('ZCOOL login is detected on cookie change without a download or reparse', collector.state.login.zcool && collector.state.inspectionId === receipt);
  const runner = collector.runTask;
  const fail = async message => {
    collector.runTask = () => ({ promise: Promise.reject(failureError(message, { operation: 'download' })), cancel() {} });
    await require('node:assert/strict').rejects(() => collector.withCookies({ command: 'download', url: URL,
      settings: { proxy: 'http://127.0.0.1:9' } }, () => {}).promise);
  };
  try {
    await fail('Read timed out');
    await until(signedIn, 'a network error expired ZCOOL credentials');
    check('ZCOOL network errors cannot invalidate login credentials', !collector.state.loginExpired.zcool);
    await fail('Login required');
    await until(expired, 'ZCOOL explicit authentication failure did not reach the account header');
    check('ZCOOL an actual authentication failure marks the matching credentials expired', collector.state.loginExpired.zcool);
  } finally { collector.runTask = runner; }
  await set('JSESSIONID', 'zcool-fixture-visitor-changed'); await collector.updateLogin();
  await until(expired, 'anonymous ZCOOL cookie changes cleared a rejected login');
  check('ZCOOL anonymous cookie changes do not reset an expired login', collector.state.loginExpired.zcool);
  await set('zcool_logon_new', 'zcool-fixture-new-logon');
  await until(signedIn, 'new ZCOOL credentials did not restore login');
  check('ZCOOL fresh credentials automatically restore the normal signed-in header', !collector.state.loginExpired.zcool);
  await collector.browserSession.cookies.remove('https://www.zcool.com.cn/', 'zcool_logon_new');
  await until(signedOut, 'ZCOOL logout did not update automatically');
  check('ZCOOL logout clears the signed-in status immediately', !collector.state.login.zcool && !collector.state.loginExpired.zcool);
  const file = path.join(app.getPath('userData'), 'zcool-import-fixture.txt');
  const expiry = Math.floor(Date.now() / 1000) + 3600;
  const text = `# Netscape HTTP Cookie File\n#HttpOnly_.zcool.com.cn\tTRUE\t/\tTRUE\t${expiry}\tzcool_mt\tzcool-fixture-import-token\n#HttpOnly_.zcool.com.cn\tTRUE\t/\tTRUE\t${expiry}\tzcool_logon_new\tzcool-fixture-import-logon\n`;
  fs.writeFileSync(file, text);
  try {
    const count = await collector.importCookies(file, 'zcool'); await until(signedIn, 'imported ZCOOL credentials did not update the account header');
    check('ZCOOL importing both login cookies updates the header and preserves the source file', count === 2 && fs.readFileSync(file, 'utf8') === text);
  } finally { fs.unlinkSync(file); }
  check('ZCOOL login updates preserve parsed resources and selections without exposing cookies', collector.state.inspectionId === receipt &&
    await ui("!document.querySelector('[data-image=\"1\"]').checked") && !/zcool-fixture-/.test(JSON.stringify(collector.publicState())));
}
async function run(collector, check, until, wait, assetRequests) {
  const source = collector.tabs.active, add = collector.queue.add;
  const ui = code => collector.window.webContents.executeJavaScript(code);
  const fetch = collector.assets.fetch;
  let spec, title, dimensionRequests = 0, reloaded = false;
  try {
    collector.assets.fetch = (value, options) => {
      if (new globalThis.URL(value).pathname === '/community/1.jpg' && options.headers?.Range === 'bytes=0-262143') {
        dimensionRequests++; reloaded = options.cache === 'reload';
        if (dimensionRequests === 1) return Promise.resolve(new Response(Buffer.from([0xff]), {
          headers: { 'content-type': 'image/jpeg', 'content-length': '2093605' },
        }));
      }
      return fetch(value, options);
    };
    const tab = collector.openTab(URL, { group: 'zcool' });
    await until(() => tab.state.site === 'zcool' && !tab.view.webContents.isLoadingMainFrame(), 'ZCOOL fixture did not load');
    await ui("document.getElementById('resources-tab').click()");
    await collector.inspect();
    check('ZCOOL page parses exactly 28 ordered body images', collector.state.result?.items[0].images.length === 28 &&
      collector.state.result.items[0].images[27].index === 28);
    check('ZCOOL snapshots exclude private stores and recommendations', !/do-not-forward|recommend.jpg/.test(JSON.stringify(collector.publicState())));
    await until(() => ui("document.querySelector('[data-preview-image]')?.naturalWidth>0 && document.querySelector('[data-image-size]')?.textContent==='大小：2.09MB'"), 'ZCOOL partial dimension fixture did not settle');
    check('ZCOOL a successful preview never substitutes its thumbnail dimensions for missing source pixels',
      await ui("document.querySelector('[data-image-dimensions]').textContent==='尺寸：未知'"));
    check('ZCOOL incomplete dimensions expose retry even when the preview and size succeed',
      await ui("!document.querySelector('[data-retry-preview=\"1\"]').hidden"));
    await ui("document.querySelector('[data-image=\"1\"]').checked=false;document.querySelector('[data-retry-preview=\"1\"]').click()");
    await until(() => ui("document.querySelector('[data-preview-image]')?.naturalWidth>0 && document.querySelector('.image-meta')?.textContent.includes('4×3')"), 'ZCOOL preview or actual dimensions missing');
    check('ZCOOL retry bypasses both partial caches and reloads source headers immediately', dimensionRequests === 2 && reloaded);
    check('ZCOOL metadata retry preserves image selection and clears the retry button on success',
      await ui("!document.querySelector('[data-image=\"1\"]').checked && document.querySelector('[data-retry-preview=\"1\"]').hidden"));
    check('ZCOOL size cards never mislabel resized files as originals', await ui("/^大小：[0-9]+[.][0-9]{2}MB$/.test(document.querySelector('[data-image-size]').textContent) && !document.querySelector('.image-meta').textContent.includes('12×9')"));
    check('ZCOOL explains the original-size limitation and exposes only web login and cookie import', await ui("document.querySelector('.collection-notice').textContent.includes('缩放图片') && !document.getElementById('web-login').hidden && document.getElementById('edge-login').hidden && !document.getElementById('import-login').hidden"));
    check('ZCOOL asset requests retain complete signed parameters and permitted origin', assetRequests.some(row =>
      row.url.includes('img.zcool.cn') && row.url.includes('k=fixture&t=fixture') && row.referer === 'https://www.zcool.com.cn/'));
    await loginChecks(collector, check, until);
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
    await require('./zcool-video-smoke.cjs').run(collector, check, until, wait, assetRequests);
    const receipt = collector.state.inspectionId;
    await tab.view.webContents.mainFrame.executeJavaScript("history.pushState({},'', '/work/ZanotherWork.html')");
    await wait(120);
    check('ZCOOL manual mode retains its completed inspection across navigation', collector.state.inspectionId === receipt);
    const stale = await collector.readPage(snapshotScript);
    check('ZCOOL stale page JSON cannot be attached to a different work', stale.mismatch === true && stale.images.length === 0);
  } finally { collector.assets.fetch = fetch; collector.queue.add = add; collector.activateTab(source.id); }
}
module.exports = { fixture, run };
