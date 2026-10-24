'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { app, desktopCapturer, session } = require('electron');
const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
async function until(check, message) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) { if (await check()) return; await wait(80); }
  throw new Error(message);
}
async function captureWindow(window) {
  const sources = await desktopCapturer.getSources({ types: ['window'], thumbnailSize: { width: 1440, height: 920 } });
  const ownWindow = sources.find(source => source.id === window.getMediaSourceId());
  if (!ownWindow || ownWindow.thumbnail.isEmpty()) throw new Error('Cannot capture the test window');
  return ownWindow.thumbnail.toPNG();
}
async function run(collector, profile, snapshotScript) {
  const results = [], warnings = [];
  const check = (name, condition) => { assert.ok(condition, name); results.push(name); };
  try {
    new Function(snapshotScript);
    await collector.healthTask.promise;
    check('Python worker connected', collector.state.health.packages.Pillow);
    check('browser and downloader use an ASCII user agent', /^[\x20-\x7e]+$/.test(collector.userAgent));
    check('local UI preload is available', await collector.window.webContents.executeJavaScript('typeof window.collector.call === "function"'));
    collector.window.show();
    await wait(750);
    check('native browser bounds follow the layout', collector.browser.getBounds().height > 400);
    collector.settings.autoParse = false;
    const note = id => ({ title: id.startsWith('0') ? '受控测试 · 笔记 A' : '受控测试 · 笔记 B',
      desc: '本页面仅用于隔离验证', user: { nickname: '测试作者' }, imageList: [
        { fileId: 'safe/resource1', urlDefault: 'https://sns-webpic-qc.xhscdn.com/display1', width: 1000, height: 1400 },
        { fileId: 'safe/resource2', urlDefault: 'https://sns-webpic-qc.xhscdn.com/display2', width: 1200, height: 1600 },
      ] });
    const a = '0123456789abcdef01234567', b = '1123456789abcdef01234567';
    const fixture = `<html><head><title>受控站点页面</title></head><body><h1>受控页面</h1><script>window.__INITIAL_STATE__=${JSON.stringify({
      note: { noteDetailMap: { [a]: { note: note(a) }, [b]: { note: note(b) } } },
      videoData: { title: '受控 B站视频', pages: [{page:1,part:'第一段'},{page:2,part:'第二段'}] },
    })}; window.__playinfo__={data:{dash:{video:[{id:16,baseUrl:'https://fixture.bilivideo.com/video.m4s',mimeType:'video/mp4',codecs:'avc1.64001e',height:360,width:640}]}}};</script></body></html>`;
    const pendingStreams = [];
    await session.defaultSession.protocol.handle('https', () => new Response('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="140"><rect width="100" height="140" fill="#eaf4ef"/></svg>', { headers: { 'content-type': 'image/svg+xml' } }));
    await collector.browserSession.protocol.handle('https', request => {
      if (new URL(request.url).pathname === '/collector-slow-resource') return new Response(new ReadableStream({
        start(controller) { pendingStreams.push(controller); },
      }), { headers: { 'content-type': 'image/png' } });
      const html = new URL(request.url).searchParams.has('slow') ? fixture.replace('</body>', '<img src="/collector-slow-resource"></body>') : fixture;
      return new Response(html, { headers: { 'content-type': 'text/html; charset=utf-8' } });
    });
    for (const url of ['https://www.youtube.com/watch?v=fixture', 'https://www.bilibili.com/video/BVfixture', `https://www.xiaohongshu.com/explore/${a}`]) {
      await collector.browser.webContents.loadURL(url);
      check('embedded navigation: ' + new URL(url).hostname, collector.browser.webContents.getURL() === url);
      if (url.includes('bilibili.com')) {
        await collector.inspect();
        check('real Python parses loaded Bilibili player formats', collector.state.result?.items[0].formats[0].height === 360);
        await wait(150);
        check('Bilibili part selector reaches the right panel', await collector.window.webContents.executeJavaScript('document.querySelectorAll("#bili-part option").length === 2'));
        const loadedPart = new Promise(resolve => collector.browser.webContents.once('did-finish-load', resolve));
        await collector.browser.webContents.executeJavaScript("history.pushState({}, '', '?p=2')");
        await loadedPart;
        check('Bilibili SPA part switch reloads matching player data', collector.documentKey === 'bilibili:BVfixture:2');
        await collector.inspect();
        check('Bilibili second part is parsed independently', collector.state.result?.items[0].currentPart === 2);
      }
    }
    check('remote webpage cannot access Node or local IPC', await collector.browser.webContents.executeJavaScript('typeof require === "undefined" && typeof window.collector === "undefined"'));
    await collector.browserSession.cookies.set({ url: 'https://www.xiaohongshu.com', domain: '.xiaohongshu.com', name: 'web_session', value: 'fixture-secret', httpOnly: true, secure: true, expirationDate: Date.now() / 1000 + 3600 });
    await collector.updateLogin();
    check('HttpOnly login detected from browser session', collector.state.login.xiaohongshu);
    await collector.inspect();
    check('real Python parses loaded XHS note', collector.state.result?.items[0].title.includes('笔记 A'));
    check('image order preserved', collector.state.result.items[0].images.map(i => i.index).join(',') === '1,2');
    await wait(150);
    check('resources reach the rendered right panel', await collector.window.webContents.executeJavaScript('document.querySelectorAll("[data-image]").length === 2'));
    const domReady = new Promise(resolve => collector.browser.webContents.once('dom-ready', resolve));
    const slowLoad = collector.browser.webContents.loadURL(`https://www.xiaohongshu.com/explore/${a}?slow=1`);
    await domReady;
    await until(() => pendingStreams.length > 0, 'slow subresource did not start');
    check('slow subresource keeps loading after DOM ready', collector.browser.webContents.isLoadingMainFrame());
    await collector.inspect();
    check('inspection works while an unrelated resource is still loading', collector.state.result?.items[0].title.includes('笔记 A'));
    pendingStreams.forEach(stream => stream.close()); await slowLoad;
    const stableReceipt = collector.state.inspectionId;
    await collector.browser.webContents.executeJavaScript("history.replaceState({}, '', '?xsec_source=pc_feed&tracking=test')");
    await wait(100);
    check('tracking-only URL changes preserve parsed resources', collector.state.inspectionId === stableReceipt && !!stableReceipt);
    // Windows test runners may have no capturable compositor surface.
    // Screenshot availability is reported separately from browser/worker assertions.
    if (process.env.YT_COLLECTOR_CAPTURE === '1') {
      let timer;
      try {
        const png = await Promise.race([captureWindow(collector.window),
          new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Window capture timed out')), 5000); })]);
        fs.writeFileSync(path.join(profile, '..', 'phase1-window.png'), png);
      } catch (error) { warnings.push(error.message); }
      finally { clearTimeout(timer); }
    }
    collector.window.hide();
    const receipt = collector.state.inspectionId;
    let resolveDownload;
    collector.queue.run = () => ({ promise: new Promise(resolve => { resolveDownload = resolve; }), cancel() { resolveDownload({}); } });
    const taskId = collector.download({ inspectionId: receipt, itemIndex: 0, indices: [2] });
    const task = collector.queue.tasks.find(t => t.id === taskId);
    check('selected images are passed to the queue', task.spec.indices.join(',') === '2');
    const realRunner = collector.withCookies.bind(collector);
    let resolveOld, oldCancelled = false;
    collector.withCookies = () => ({ promise: new Promise(resolve => { resolveOld = resolve; }), cancel() { oldCancelled = true; } });
    const pending = collector.inspect();
    await until(() => !!resolveOld, 'inspection did not start');
    await collector.browser.webContents.executeJavaScript(`history.pushState({}, '', '/explore/${b}')`);
    await wait(100);
    resolveOld({ site: 'xiaohongshu', items: [{ title: 'stale' }] }); await pending;
    check('stale SPA inspection cannot overwrite current page', oldCancelled && collector.state.result === null);
    check('navigating does not cancel an active download', task.status === 'running');
    assert.throws(() => collector.download({ inspectionId: receipt, itemIndex: 0, indices: [1] }));
    results.push('stale download buttons rejected');
    collector.withCookies = realRunner;
    await collector.inspect();
    check('new SPA note is parsed independently', collector.state.result.items[0].title.includes('笔记 B'));
    resolveDownload({ folder: profile, count: 1 }); await wait(100);
    check('background download queue completes', task.status === 'completed');
    collector.persist();
    const saved = fs.readFileSync(path.join(profile, 'history.json'), 'utf8');
    check('history does not store cookie snapshots', !saved.includes('fixture-secret') && !saved.includes('cookieFile'));
    check('temporary cookie files are removed', fs.readdirSync(path.join(profile, 'tasks')).length === 0);
    await collector.browserSession.cookies.flushStore();
    check('persistent browser profile created', fs.existsSync(path.join(profile, 'Partitions')));
    fs.writeFileSync(path.join(profile, '..', 'smoke-result.json'), JSON.stringify({ ok: true, results, warnings, profile }, null, 2));
    console.log(JSON.stringify({ ok: true, checks: results.length, profile }));
    collector.browser.webContents.close(); collector.window.destroy(); app.exit(0);
  } catch (error) {
    fs.writeFileSync(path.join(profile, '..', 'smoke-result.json'), JSON.stringify({ ok: false, results, warnings, error: error.stack, profile }, null, 2));
    console.error(error); app.exit(1);
  }
}
module.exports = { run };
