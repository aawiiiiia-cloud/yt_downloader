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
  for (const key of [...url.searchParams.keys()]) if (/token|signature|^sig$/i.test(key)) url.searchParams.set(key, '[redacted]');
  return url.href;
}
async function run(collector, profile) {
  const results = [];
  collector.window.showInactive();
  collector.settings.autoParse = false;
  const targets = process.env.YT_COLLECTOR_TEST_TARGETS ? JSON.parse(fs.readFileSync(process.env.YT_COLLECTOR_TEST_TARGETS, 'utf8')) : [
    ['youtube', 'https://www.youtube.com/watch?v=jNQXAC9IVRw'],
    ['bilibili', 'https://www.bilibili.com/video/BV12iFezdEZS'],
    ['xiaohongshu', 'https://www.xiaohongshu.com/explore'],
  ];
  if (!Array.isArray(targets) || targets.length > 3 || !targets.length || targets.some(row =>
    !Array.isArray(row) || row.length !== 2 || siteForUrl(row[1]) !== row[0])) throw new Error('验收链接配置无效');
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
            const loaded = () => resolve();
            collector.browser.webContents.once('dom-ready', loaded);
            collector.browser.webContents.loadURL(url).then(loaded, error => { if (error.errno !== -3) reject(error); });
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
          await collector.inspect();
          result.parsed = !!collector.state.result?.items?.length;
          result.items = collector.state.result?.items?.length || 0;
          result.formats = collector.state.result?.items?.[0]?.formats?.length || 0;
          result.kind = collector.state.result?.items?.[0]?.kind;
          result.contentTitle = collector.state.result?.items?.[0]?.title;
          result.images = collector.state.result?.items?.[0]?.images?.length || 0;
          result.duration = collector.state.result?.items?.[0]?.duration;
          if (collector.state.error) result.error = collector.state.error;
          if (!result.parsed && !result.error) result.error = '解析期间页面发生变化，请在当前内容加载稳定后重试';
          if (process.env.YT_COLLECTOR_TEST_DOWNLOAD === '1' && result.parsed) {
            const item = collector.state.result.items[0];
            const formats = (item.formats || []).filter(f => f.video && f.height && f.height <= 360);
            const format = formats.find(f => f.audio) || formats[0];
            if (item.kind !== 'images' && !format) throw new Error('没有 360p 或以下的小样本画质，不执行下载');
            const indices = item.kind === 'images' ? (process.env.YT_COLLECTOR_TEST_ALL_IMAGES === '1' ? item.images.map(image => image.index) : [item.images[0].index]) : [];
            collector.settings.saveDir = path.join(profile, 'downloads', site);
            const id = collector.download({ inspectionId: collector.state.inspectionId, itemIndex: 0,
              formatId: format?.id, indices });
            const task = collector.queue.tasks.find(t => t.id === id);
            const deadline = Date.now() + 90000;
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
  } finally {
    collector.handles.forEach(task => task.cancel());
    const deadline = Date.now() + 45000;
    while (collector.handles.size && Date.now() < deadline) await wait(150);
    const failed = results.some(result => result.error || !result.browsing || (result.requiresParse && !result.parsed));
    collector.browser.webContents.close(); collector.window.destroy(); app.exit(failed ? 1 : 0);
  }
}
module.exports = { run };
