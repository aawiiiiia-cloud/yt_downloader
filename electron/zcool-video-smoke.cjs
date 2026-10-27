'use strict';
const fs = require('node:fs'), path = require('node:path');
const { app } = require('electron');
const URL = 'https://www.zcool.com.cn/work/ZNzQyNjY2ODg=.html';
async function run(collector, check, until, wait, assetRequests) {
  // Backend tests use a real MP4; this fixture checks renderer/queue behavior.
  const ui = code => collector.window.webContents.executeJavaScript(code);
  const original = collector.cache.get(collector.state.inspectionId);
  const context = { ...original.context, videos: [{ id: 123, url: 'https://video.zcool.cn/alivod/fixture.mp4?k=private-video&t=fixture' }] };
  const video = { id: 'fixture-video', mediaId: 'video-123', kind: 'video', url: URL,
    title: '受控站酷作品 · 视频 1', author: '测试作者', duration: 96.04, subtitles: [],
    thumbnail: 'https://video.zcool.cn/alivod/fixture.mp4!vframeOffset0?k=cover&t=fixture',
    formats: [{ id: 'mp4', width: 1920, height: 1080, video: true, audio: true, ext: 'mp4', size: 45081788, sizeEstimated: false }] };
  const present = items => {
    const id = require('node:crypto').randomUUID(), result = { ...original.result, items,
      warnings: items.some(item => item.kind === 'images') ? original.result.warnings : [] };
    collector.cache.set(id, { ...original, result, context });
    Object.assign(collector.state, { result, inspectionId: id }); collector.publish();
  };
  present([...original.result.items, video]);
  await until(() => ui("document.querySelector('#part-select')?.options.length===2"), 'ZCOOL mixed resource picker missing');
  check('ZCOOL images and native videos are separate selectable resources',
    await ui("document.querySelector('#part-select').options[0].text.includes('图片集') && document.querySelector('#part-select').options[1].text.includes('视频 1')"));
  await ui("document.querySelector('#part-select').value='1';document.querySelector('#part-select').dispatchEvent(new Event('change',{bubbles:true}))");
  await until(() => ui("document.querySelector('.cover-frame img')?.naturalWidth>0 && document.querySelector('#video-size')?.textContent==='45.08 MB'"), 'ZCOOL native video preview/size missing');
  check('ZCOOL video cards show duration, real resolution and full muxed file size',
    await ui("document.querySelector('.media-summary').textContent.includes('01:36') && document.querySelector('#format-select').textContent.includes('1080p')"));
  check('ZCOOL video descriptions do not show image-only resolution warnings',
    await ui("!document.querySelector('#resource-size-note').textContent.includes('缩放图') && ![...document.querySelectorAll('.collection-notice')].some(row=>row.textContent.includes('缩放图片'))"));
  check('ZCOOL video frame covers preserve signed URLs and the source origin', assetRequests.some(row =>
    row.url.includes('mp4!vframeOffset0?k=cover&t=fixture') && row.referer === 'https://www.zcool.com.cn/'));
  const add = collector.queue.add; let queued;
  collector.queue.add = value => { queued = value; return 'zcool-native-video'; };
  try {
    await ui("document.querySelector('#format-select').value='mp4';document.querySelector('#download').click()");
    await until(() => queued?.kind === 'video', 'ZCOOL video did not enter the queue');
    check('ZCOOL native video tasks retain work, video ID and private context',
      queued.mediaId === 'video-123' && queued.formatId === 'mp4' && queued.url === URL &&
      queued.context.videos[0].url.includes('private-video') && !JSON.stringify(collector.publicState()).includes('private-video'));
    await until(() => ui("!document.querySelector('#download').disabled"), 'ZCOOL video download control stayed busy');
    queued = null;
    await ui("document.querySelector('#audio-only').checked=true;document.querySelector('#download').click()");
    await until(() => queued?.settings.audioOnly, 'ZCOOL MP3 selection was lost');
    check('ZCOOL audio-only tasks use the same site queue and video identity', queued.mediaId === 'video-123' && queued.kind === 'video');
    await until(() => ui("!document.querySelector('#download').disabled"), 'ZCOOL audio control stayed busy');
  } finally { collector.queue.add = add; }
  await ui("document.getElementById('resources-tab').click()");
  present([video]);
  await until(() => ui("!document.querySelector('#part-select') && document.querySelector('.cover-frame img')?.naturalWidth>0"), 'ZCOOL video-only resources did not render');
  check('ZCOOL video-only works render without an image collection', await ui("document.querySelector('#download').textContent==='下载所选'"));
  await ui("document.getElementById('resources-panel').scrollTop=0"); await wait(100);
  fs.writeFileSync(path.join(app.getPath('userData'), '..', 'zcool-video-panel.png'), (await collector.window.webContents.capturePage()).toPNG());
}
module.exports = { run };
