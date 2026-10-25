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
    const imageBytes = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAE0lEQVR4nGO8/Po+AwwwwVnoHABhQQKjPQkWWQAAAABJRU5ErkJggg==', 'base64');
    const assetRequests = []; let coverFailed = false, youtubeLoggedIn = false;
    const note = id => ({ title: id.startsWith('0') ? '受控测试 · 笔记 A' : '受控测试 · 笔记 B',
      desc: '本页面仅用于隔离验证', user: { nickname: '测试作者' }, imageList: [
        { fileId: 'safe/resource1', urlDefault: 'https://sns-webpic-qc.xhscdn.com/display1', width: 1000, height: 1400 },
        { fileId: 'safe/resource2', urlDefault: 'https://sns-webpic-qc.xhscdn.com/display2', width: 1200, height: 1600 },
      ] });
    const a = '0123456789abcdef01234567', b = '1123456789abcdef01234567';
    const fixture = `<html><head><title>受控站点页面</title></head><body><h1>受控页面</h1><script>window.__INITIAL_STATE__=${JSON.stringify({
      note: { noteDetailMap: { [a]: { note: note(a) }, [b]: { note: note(b) } } },
      videoData: { title: '受控 B站视频', pic:'http://i0.hdslb.com/cover.png', duration:3527, pages: [{page:1,part:'第一段',duration:3527},{page:2,part:'第二段',duration:3601}] },
      aweme: { detail: { aweme_id: '1234567890123456789', desc: '受控抖音视频', author: { nickname: '测试作者' },
        video: { duration: 58000, width: 640, height: 360,
          play_addr: { url_list: ['https://fixture.douyinvod.com/video.mp4'], data_size: 340000 },
          cover: { url_list: ['https://p3.douyinpic.com/cover.png'] } } } },
    })}; window.__playinfo__={data:{dash:{video:[{id:16,baseUrl:'https://fixture.bilivideo.com/video.m4s',bandwidth:800000,mimeType:'video/mp4',codecs:'avc1.64001e',height:360,width:640}]}}};</script></body></html>`;
    const pendingStreams = [];
    await session.defaultSession.protocol.handle('https', () => new Response('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="140"><rect width="100" height="140" fill="#eaf4ef"/></svg>', { headers: { 'content-type': 'image/svg+xml' } }));
    await collector.browserSession.protocol.handle('https', request => {
      const resource = new URL(request.url);
      if (resource.hostname === 'mir-s3-cdn-cf.behance.net' || resource.hostname.endsWith('.hdslb.com') || resource.hostname.endsWith('.xhscdn.com') || resource.hostname.endsWith('.ytimg.com') || resource.hostname.endsWith('.vimeocdn.com') || resource.hostname.endsWith('.douyinpic.com')) {
        assetRequests.push({ url:request.url, method:request.method, referer:request.headers.get('referer') });
        if (resource.hostname.endsWith('.hdslb.com') && !coverFailed) { coverFailed = true; return new Response('unavailable', {status:503}); }
        return new Response(request.method === 'HEAD' ? null : imageBytes, { headers: { 'content-type':'image/png', 'content-length':request.method === 'HEAD' ? '340000' : String(imageBytes.length) } });
      }
      if (resource.hostname === 'www.youtube.com' && resource.pathname === '/') return new Response(`<script>ytcfg.set({"LOGGED_IN":${youtubeLoggedIn}})</script>`, {headers:{'content-type':'text/html'}});
      if (resource.hostname === 'www.behance.net') return new Response(`<html><head><title>受控 Behance 作品</title></head><body><script id="beconfig-store_state" type="application/json">${JSON.stringify({project:{project:require('./fixtures/behance.json')}})}</script></body></html>`, {headers:{'content-type':'text/html; charset=utf-8'}});
      if (resource.hostname === 'player.vimeo.com') return new Response(`<html><body><script>var playerConfig=${JSON.stringify({video:{id:123456,title:'受控外部视频',duration:6,thumbs:{240:'https://i.vimeocdn.com/video/external.png'}},request:{files:{progressive:[{quality:'240p',width:426,height:240,url:'https://vod-test.vimeocdn.com/external.mp4?token=privateVimeoStream'}]}}})}</script></body></html>`,{headers:{'content-type':'text/html; charset=utf-8'}});
      if (new URL(request.url).pathname === '/collector-slow-resource') return new Response(new ReadableStream({
        start(controller) { pendingStreams.push(controller); },
      }), { headers: { 'content-type': 'image/png' } });
      const html = new URL(request.url).searchParams.has('slow') ? fixture.replace('</body>', '<img src="/collector-slow-resource"></body>') : fixture;
      return new Response(html, { headers: { 'content-type': 'text/html; charset=utf-8' } });
    });
    for (const url of ['https://www.youtube.com/watch?v=fixture', 'https://www.bilibili.com/video/BVfixture', `https://www.xiaohongshu.com/explore/${a}`]) {
      collector.navigate(url);
      await until(() => collector.browser.webContents.getURL() === url && !collector.browser.webContents.isLoadingMainFrame(), 'site did not load');
      check('embedded navigation: ' + new URL(url).hostname, collector.browser.webContents.getURL() === url);
      if (url.includes('youtube.com')) {
        const login = collector.login; let edgeRequested = false;
        collector.login = async site => { edgeRequested = site === 'youtube'; };
        await collector.window.webContents.executeJavaScript('document.getElementById("edge-login").click()');
        await until(() => edgeRequested, 'YouTube login did not use Edge');
        check('YouTube login button invokes Edge without replacing the current page', collector.browser.webContents.getURL() === url);
        collector.login = login;
        const proxy = collector.effectiveProxy;
        collector.effectiveProxy = async () => { throw new Error('controlled proxy failure'); };
        await assert.rejects(() => collector.login('youtube'), /controlled proxy failure/);
        check('failed Edge setup clears the busy state for retry', collector.state.loginRunning === null && collector.loginTask === null);
        collector.effectiveProxy = proxy;
        const inspectionId = 'youtube-auth-fixture';
        collector.state.result = {site:'youtube',items:[{kind:'video',url,title:'YouTube auth fixture',formats:[]}]};
        collector.state.inspectionId = inspectionId;
        collector.cache.set(inspectionId,{result:collector.state.result,url});
        await assert.rejects(() => collector.download({inspectionId,itemIndex:0}), {code:'YOUTUBE_AUTH_REQUIRED'});
        check('unsigned YouTube downloads never enter the queue', collector.queue.tasks.length === 0);
        await collector.browserSession.cookies.set({url:'https://www.youtube.com',domain:'.youtube.com',name:'SID',value:'controlled-cookie',secure:true,httpOnly:true,expirationDate:Date.now()/1000+3600});
        await assert.rejects(() => collector.download({inspectionId,itemIndex:0}), {code:'YOUTUBE_AUTH_REQUIRED'});
        check('a cookie without server-confirmed login is rejected', collector.youtubeAuth.status === 'expired');
        youtubeLoggedIn = true; await collector.youtubeAuth.ensure(true);
        check('server-confirmed YouTube login enables the gate', collector.youtubeAuth.status === 'verified');
        await collector.browserSession.cookies.remove('https://www.youtube.com', 'SID');
        const blocked = collector.withCookies({command:'download',url,settings:{}}, () => {});
        await assert.rejects(() => blocked.promise, {code:'YOUTUBE_AUTH_REQUIRED'});
        check('logout is rechecked before starting a queued Python download', !collector.state.login.youtube || collector.youtubeAuth.status !== 'verified');
      }
      if (url.includes('bilibili.com')) {
        await collector.inspect();
        check('real Python parses loaded Bilibili player formats', collector.state.result?.items[0].formats[0].height === 360);
        await wait(150);
        await until(() => collector.window.webContents.executeJavaScript('!document.querySelector("[data-retry-preview=cover]").hidden'), 'cover failure was not exposed');
        check('cover failures show a retry control', true);
        await collector.window.webContents.executeJavaScript('document.querySelector("[data-retry-preview=cover]").click()');
        await until(() => collector.window.webContents.executeJavaScript('document.querySelector(".cover-frame img").naturalWidth > 0'), 'cover did not decode after retry');
        check('HTTP Bilibili cover loads after upgrade and retry', assetRequests.some(row => row.url === 'https://i0.hdslb.com/cover.png' && row.referer === 'https://www.bilibili.com/'));
        check('video displays precise duration and estimated size', await collector.window.webContents.executeJavaScript('document.querySelector(".media-summary").textContent.includes("58:47") && document.getElementById("video-size").textContent.includes("MB")'));
        check('Bilibili part selector reaches the right panel', await collector.window.webContents.executeJavaScript('document.querySelectorAll("#bili-part option").length === 2'));
        await collector.browser.webContents.executeJavaScript('const delayedPlayerData=window.__playinfo__; delete window.__playinfo__; setTimeout(()=>{window.__playinfo__=delayedPlayerData},350)');
        await collector.inspect();
        check('inspection waits for Bilibili player data arriving after DOM ready', collector.state.result?.items[0].formats[0].height === 360);
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
    await until(() => collector.window.webContents.executeJavaScript('[...document.querySelectorAll("[data-image-size]")].some(row => row.textContent.includes("0.34 MB"))'), 'original image sizes were not displayed');
    check('image size comes from the original rather than the smaller preview', true);
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
    const taskId = await collector.download({ inspectionId: receipt, itemIndex: 0, indices: [2] });
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
    await assert.rejects(() => collector.download({ inspectionId: receipt, itemIndex: 0, indices: [1] }));
    results.push('stale download buttons rejected');
    collector.withCookies = realRunner;
    await collector.inspect();
    check('new SPA note is parsed independently', collector.state.result.items[0].title.includes('笔记 B'));
    await require('./tabs-smoke.cjs').run(collector, check, until, wait);
    youtubeLoggedIn = false;
    await require('./workspace-smoke.cjs').run(collector, check, until, wait, profile);
    await require('./new-sites-smoke.cjs').run(collector, check, until, wait);
    await require('./behance-smoke.cjs').run(collector, check, until, wait);
    resolveDownload({ folder: profile, count: 1 }); await wait(100);
    check('background download queue completes', task.status === 'completed');
    await require('./task-failure-smoke.cjs').run(collector, check, until, wait, profile);
    collector.persist();
    const saved = fs.readFileSync(path.join(profile, 'history.json'), 'utf8');
    check('history does not store cookie snapshots', !saved.includes('fixture-secret') && !saved.includes('cookieFile'));
    check('temporary cookie files are removed', fs.readdirSync(path.join(profile, 'tasks')).length === 0);
    await collector.browserSession.cookies.flushStore();
    check('persistent browser profile created', fs.existsSync(path.join(profile, 'Partitions')));
    fs.writeFileSync(path.join(profile, '..', 'smoke-result.json'), JSON.stringify({ ok: true, results, warnings, profile }, null, 2));
    console.log(JSON.stringify({ ok: true, checks: results.length, profile }));
    collector.destroyViews(); collector.window.destroy(); app.exit(0);
  } catch (error) {
    fs.writeFileSync(path.join(profile, '..', 'smoke-result.json'), JSON.stringify({ ok: false, results, warnings, error: error.stack, profile }, null, 2));
    console.error(error); app.exit(1);
  }
}
module.exports = { run };
