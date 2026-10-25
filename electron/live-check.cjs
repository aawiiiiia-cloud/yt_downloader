'use strict';
// Public-site metadata check; optional sample download requires YT_COLLECTOR_TEST_DOWNLOAD=1.
// Never imports the user's login credentials.
const fs = require('node:fs');
const path = require('node:path');
const { app } = require('electron');
const { execFileSync } = require('node:child_process');
const { createHash } = require('node:crypto');
const { contentKey, siteForUrl } = require('./model.cjs');
const { pythonExecutable } = require('./bridge.cjs');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
function reportUrl(value) {
  const url = new URL(value);
  for (const key of [...url.searchParams.keys()]) if (/token|signature|^sig$|^h$|bogus/i.test(key)) url.searchParams.set(key, '[redacted]');
  return url.href;
}
async function run(collector, profile) {
  const results = [], visited = [];
  collector.window.showInactive();
  collector.settings.autoParse = false;
  const targets = process.env.YT_COLLECTOR_TEST_TARGETS ? JSON.parse(fs.readFileSync(process.env.YT_COLLECTOR_TEST_TARGETS, 'utf8')) : [
    ['youtube', 'https://www.youtube.com/watch?v=jNQXAC9IVRw'],
    ['bilibili', 'https://www.bilibili.com/video/BV12iFezdEZS'],
    ['xiaohongshu', 'https://www.xiaohongshu.com/explore'],
  ];
  if (!Array.isArray(targets) || targets.length > 5 || !targets.length || targets.some(row =>
    !Array.isArray(row) || row.length !== 2 || siteForUrl(row[1]) !== row[0])) throw new Error('验收链接配置无效');
  if (!targets.some(([site]) => !process.env.YT_COLLECTOR_TEST_SITE || process.env.YT_COLLECTOR_TEST_SITE === site)) throw new Error('验收配置中没有所选站点的链接');
  const maxHeight = Number(process.env.YT_COLLECTOR_TEST_MAX_HEIGHT || 360);
  if (!Number.isInteger(maxHeight) || maxHeight < 144 || maxHeight > 2160) throw new Error('验收画质上限无效');
  try {
    for (const [site, url] of targets) {
      if (process.env.YT_COLLECTOR_TEST_SITE && process.env.YT_COLLECTOR_TEST_SITE !== site) continue;
      console.log('LIVE: loading ' + site);
      const needsParse = !!contentKey(url);
      const result = { site, requestedUrl: reportUrl(url), requiresParse: needsParse, browsing: false, parsed: false };
      let timer;
      try {
        await Promise.race([
          new Promise((resolve, reject) => {
            const tab = collector.openTab(url, { group: site, reuse: true }), web = tab.view.webContents;
            const loaded = () => { web.removeListener('did-fail-load', failed); resolve(); };
            const failed = (_event, code, description, _url, main) => {
              if (main && code !== -3) { web.removeListener('dom-ready', loaded); reject(new Error(description)); }
            };
            web.once('dom-ready', loaded); web.on('did-fail-load', failed);
          }),
          new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('公开页面加载超过 35 秒')), 35000); }),
        ]);
        clearTimeout(timer); await wait(1600);
        console.log('LIVE: loaded ' + site);
        result.title = collector.browser.webContents.getTitle(); result.browsing = true;
        result.finalUrl = reportUrl(collector.browser.webContents.getURL());
        result.pageSummary = await collector.readPage('document.body.innerText.slice(0, 180)');
        if (needsParse) {
          console.log('LIVE: extracting ' + site);
          if (site === 'behance') {
            const context = await collector.readPage(require('./behance-page.cjs').snapshotScript);
            fs.writeFileSync(path.join(profile, '..', `behance-browser-context-${contentKey(url).split(':')[1]}.json`), JSON.stringify(context));
          }
          if (site === 'bilibili') console.log('LIVE: player data', await collector.readPage('({video:!!window.__INITIAL_STATE__?.videoData,play:!!window.__playinfo__})'));
          await collector.inspect();
          result.parsed = !!collector.state.result?.items?.length;
          result.items = collector.state.result?.items?.length || 0;
          result.formats = collector.state.result?.items?.[0]?.formats?.length || 0;
          result.kind = collector.state.result?.items?.[0]?.kind;
          result.contentTitle = collector.state.result?.items?.[0]?.title;
          result.images = collector.state.result?.items?.[0]?.images?.length || 0;
          result.duration = collector.state.result?.items?.[0]?.duration;
          if (site === 'behance') result.resourceSummary = collector.state.result?.items?.map(item => ({kind:item.kind, images:item.images?.length, formats:item.formats?.length, duration:item.duration, provider:item.provider}));
          visited.push({ tab: collector.tabs.active, receipt: collector.state.inspectionId, url: collector.state.url });
          if (collector.state.error) result.error = collector.state.error;
          if (!result.parsed && !result.error) result.error = '解析期间页面发生变化，请在当前内容加载稳定后重试';
          if (process.env.YT_COLLECTOR_TEST_PREVIEW === '1' && result.parsed) {
            const item = collector.state.result.items[0];
            const args = { inspectionId: collector.state.inspectionId, itemIndex: 0, imageIndex: item.images?.[0]?.index };
            const preview = await collector.asset(args, 'preview');
            result.previewLoaded = preview.dataUrl.startsWith('data:image/');
            if (item.kind === 'images') result.originalSize = (await collector.asset(args, 'size')).size;
            const deadline = Date.now() + 15000;
            const readyPreview = '(() => { const img = document.querySelector("[data-preview-image]"); return !!img?.complete && img.naturalWidth > 0 && !img.hidden; })()';
            while (Date.now() < deadline && !await collector.window.webContents.mainFrame.executeJavaScript(readyPreview)) await wait(200);
            result.previewDecodedInPanel = await collector.window.webContents.mainFrame.executeJavaScript(readyPreview);
            if (item.kind === 'images') {
              const visibleState = `(() => { const area = document.getElementById('resources-panel').getBoundingClientRect(); const images = [...document.querySelectorAll('[data-preview-image]')].filter(img => {const r=img.parentElement.getBoundingClientRect(); return r.top<area.bottom && r.bottom>area.top;}); return {visible:images.length,decoded:images.filter(img=>img.complete && img.naturalWidth>0 && !img.hidden).length}; })()`;
              const until = Date.now() + 15000;
              while (Date.now() < until) {
                result.visiblePreviews = await collector.window.webContents.mainFrame.executeJavaScript(visibleState);
                if (result.visiblePreviews.visible === result.visiblePreviews.decoded) break;
                await wait(200);
              }
              if (result.visiblePreviews.visible !== result.visiblePreviews.decoded) throw new Error('部分可见图片预览尚未加载成功');
            }
            result.panelMetadata = await collector.window.webContents.mainFrame.executeJavaScript('document.querySelector(".media-summary")?.textContent || document.querySelector("[data-image-size]")?.textContent');
            if (!result.previewDecodedInPanel) throw new Error('资源已取得，但右侧预览未能解码');
            try {
              const rect = await collector.window.webContents.mainFrame.executeJavaScript('(() => { const r = document.querySelector("aside").getBoundingClientRect(); return {x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)}; })()');
              let screenshotTimer;
              const screenshot = await Promise.race([collector.window.webContents.capturePage(rect),
                new Promise((_, reject) => { screenshotTimer = setTimeout(() => reject(new Error('截图超时')), 5000); })]).finally(() => clearTimeout(screenshotTimer));
              const name = site === 'behance' ? `preview-behance-${contentKey(url).split(':')[1]}-panel.png` : `preview-${site}-panel.png`;
              fs.writeFileSync(path.join(profile, '..', name), screenshot.toPNG());
            } catch (error) { result.screenshotNote = error.message || String(error); }
          }
          if (process.env.YT_COLLECTOR_TEST_DOWNLOAD === '1' && result.parsed) {
            const item = collector.state.result.items[0];
            const formats = (item.formats || []).filter(f => f.video && f.height && f.height <= maxHeight).sort((a,b) => a.height-b.height);
            const format = formats.find(f => f.audio) || formats[0];
            if (item.kind !== 'images' && !format) throw new Error(`没有 ${maxHeight}p 或以下的小样本画质，不执行下载`);
            const expectedSize = format?.size || (format?.bitrate * 1000 * item.duration / 8);
            if (expectedSize > 64000000) throw new Error('该验收画质预计超过 64 MB，不执行样本下载');
            const indices = item.kind === 'images' ? (process.env.YT_COLLECTOR_TEST_ALL_IMAGES === '1' ? item.images.map(image => image.index) : [item.images[0].index]) : [];
            collector.settings.saveDir = path.join(profile, 'downloads', site);
            const id = await collector.download({ inspectionId: collector.state.inspectionId, itemIndex: 0,
              formatId: format?.id, indices });
            const task = collector.queue.tasks.find(t => t.id === id);
            const deadline = Date.now() + 180000;
            while (['queued', 'running', 'cancelling'].includes(task.status) && Date.now() < deadline) await wait(250);
            if (task.status !== 'completed') { collector.queue.cancel(id); throw new Error(task.error || '样本下载未在限定时间完成'); }
            if (item.kind === 'images') {
              const code = `import json,sys,hashlib,pillow_heif; from pathlib import Path; from PIL import Image
pillow_heif.register_heif_opener()
files=sorted(p for p in Path(sys.argv[1]).iterdir() if p.suffix.lower() in {'.jpg','.jpeg','.png','.webp','.heic','.heif'})
assert len(files)==int(sys.argv[2]), 'Selected image count mismatch'
images=[]
for p in files:
 with Image.open(p) as im: im.verify()
 with Image.open(p) as im:
  im.load(); images.append({'name':p.name,'width':im.width,'height':im.height,'format':im.format,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
print(json.dumps({'decodedImages':images,'bytes':sum(i['bytes'] for i in images)}))`;
              const verified = JSON.parse(execFileSync(pythonExecutable(), ['-B', '-X', 'utf8', '-c', code, task.result.folder, String(indices.length)], { timeout: 30000, windowsHide: true, encoding: 'utf8' }));
              Object.assign(result, verified, { downloaded: true, decoded: true, selectedImages: indices.length });
            } else {
              const file = fs.readdirSync(collector.settings.saveDir).find(name => /\.(mp4|webm|mkv)$/.test(name));
              if (!file) throw new Error('没有生成完整视频文件');
              const fullPath = path.join(collector.settings.saveDir, file);
              execFileSync('ffmpeg', ['-nostdin', '-v', 'error', '-xerror', '-i', fullPath, '-f', 'null', '-'], { timeout: 30000, windowsHide: true });
              result.downloaded = true; result.decoded = true; result.bytes = fs.statSync(fullPath).size;
              result.sha256 = createHash('sha256').update(fs.readFileSync(fullPath)).digest('hex');
            }
          }
        } else result.note = '主页浏览检查；具体笔记及登录态需使用用户提供的链接验收';
      } catch (error) { clearTimeout(timer); console.log('LIVE: page error', error?.message || String(error)); collector.browser.webContents.stop(); result.error = error?.message || String(error); }
      results.push(result);
      fs.writeFileSync(path.join(profile, '..', 'live-result.json'), JSON.stringify({ results, profile }, null, 2));
      fs.writeFileSync(path.join(profile, '..', `live-${site}-result.json`), JSON.stringify(result, null, 2));
      if (result.downloaded) fs.writeFileSync(path.join(profile, '..', `live-${site}-download-result.json`), JSON.stringify(result, null, 2));
      console.log(JSON.stringify(result));
    }
    if (process.env.YT_COLLECTOR_TEST_TABS === '1') {
      for (const entry of visited) {
        const others = visited.filter(row => row !== entry);
        collector.switchSite(entry.tab.group);
        await wait(200);
        const samePage = collector.tabs.active === entry.tab && contentKey(collector.state.url) === contentKey(entry.url);
        const sameResult = !!entry.receipt && collector.state.inspectionId === entry.receipt;
        const backgroundMuted = others.every(row => !row.tab.view.getVisible() && row.tab.view.webContents.isAudioMuted());
        const backgroundPaused = (await Promise.all(others.map(row => collector.readPage("[...document.querySelectorAll('video,audio')].every(media => media.paused)", row.tab)))).every(Boolean);
        if (!samePage || !sameResult || !backgroundMuted || !backgroundPaused) throw new Error('真实网站标签恢复检查失败：' + entry.tab.group);
        results.find(row => row.site === entry.tab.group).tabsRestored = true;
        results.find(row => row.site === entry.tab.group).backgroundPlaybackPaused = backgroundPaused;
        if (process.env.YT_COLLECTOR_TEST_LINK_TABS === '1') {
          const links = await collector.readPage("[...document.querySelectorAll('a[href]')].map(anchor => anchor.href).slice(0,2500)", entry.tab);
          const next = links.find(url => siteForUrl(url) === entry.tab.group && contentKey(url) &&
            contentKey(url) !== contentKey(entry.url) && !collector.tabs.find(url, entry.tab.group));
          const report = results.find(row => row.site === entry.tab.group);
          if (!next) report.linkTabCheck = 'no alternate detail link on this page';
          else {
            await entry.tab.view.webContents.mainFrame.executeJavaScript(`
              [...document.querySelectorAll('a[href]')].find(anchor => anchor.href === ${JSON.stringify(next)}).click()`, true);
            const deadline = Date.now() + 5000;
            while (Date.now() < deadline && collector.tabs.active === entry.tab) await wait(100);
            const opened = collector.tabs.active;
            if (opened === entry.tab || opened.group !== entry.tab.group || contentKey(opened.url) !== contentKey(next) ||
              contentKey(entry.tab.view.webContents.getURL()) !== contentKey(entry.url) || entry.tab.state.inspectionId !== entry.receipt)
              throw new Error('真实网站详情链接新标签检查失败：' + entry.tab.group);
            report.linkOpenedInGroup = true;
            collector.closeTab(opened.id); collector.activateTab(entry.tab.id);
            console.log('LIVE: real detail link opened a group tab and retained its source: ' + entry.tab.group);
          }
        }
      }
      fs.writeFileSync(path.join(profile, '..', process.env.YT_COLLECTOR_TEST_LINK_TABS === '1' ? 'live-link-tabs-result.json' : 'live-tabs-result.json'), JSON.stringify({ results, profile }, null, 2));
      if (visited.length) {
        collector.switchSite(visited[0].tab.group); await wait(250);
        fs.writeFileSync(path.join(profile, '..', 'site-tabs-toolbar.png'),
          (await collector.window.webContents.capturePage({ x: 0, y: 0, width: 1440, height: 198 })).toPNG());
      }
      console.log('LIVE: real-site group switching preserves all page views and resource receipts');
    }
  } finally {
    collector.handles.forEach(task => task.cancel());
    const deadline = Date.now() + 45000;
    while (collector.handles.size && Date.now() < deadline) await wait(150);
    const failed = results.some(result => result.error || !result.browsing || (result.requiresParse && !result.parsed));
    collector.destroyViews(); collector.window.destroy(); app.exit(failed ? 1 : 0);
  }
}
module.exports = { run };
