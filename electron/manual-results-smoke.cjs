'use strict';
const assert = require('node:assert/strict');

async function run(collector, check, until, wait) {
  const tab = collector.tabs.active, web = tab.view.webContents;
  const originalUrl = web.getURL(), settings = { ...collector.settings };
  const initial = await web.mainFrame.executeJavaScript('window.__INITIAL_STATE__');
  const ui = code => collector.window.webContents.executeJavaScript(code);
  const move = async (id, title) => {
    const detail = { ...initial.aweme.detail, aweme_id: id, desc: title };
    await web.mainFrame.executeJavaScript(`window.__INITIAL_STATE__.aweme.detail=${JSON.stringify(detail)}; history.pushState({}, '', '/jingxuan?modal_id=${id}')`);
    await wait(120);
  };
  const saveAutomatic = autoParse => collector.invoke('settings', { proxy: settings.proxy, imageFormat: settings.imageFormat, autoParse });
  try {
    await saveAutomatic(false);
    const oldReceipt = tab.state.inspectionId, oldResult = tab.state.result;
    const oldSnapshot = collector.cache.get(oldReceipt), format = oldResult.items[0].formats[0].id;
    await ui(`document.getElementById('format-select').value=${JSON.stringify(format)};document.getElementById('audio-only').checked=true;document.getElementById('subtitles').checked=true;document.getElementById('audio-only').dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('.content-title').dataset.retained='yes'`);
    await move('5234567890123456789', '手动更新后的视频 B');
    await wait(1000);
    check('manual mode keeps the completed snapshot and video choices across short-video swipes',
      tab.state.inspectionId === oldReceipt && tab.state.result === oldResult && await ui(`document.querySelector('.content-title').dataset.retained==='yes'&&document.getElementById('format-select').value===${JSON.stringify(format)}&&document.getElementById('audio-only').checked&&document.getElementById('subtitles').checked`));
    check('a retained result is visibly identified as belonging to the previous video',
      !collector.resultMatchesPage(tab) && await ui("!document.getElementById('retained-result-note').hidden"));
    check('retained previews remain usable after a swipe', (await collector.asset({ inspectionId: oldReceipt, itemIndex: 0 }, 'preview')).dataUrl.startsWith('data:image/'));
    const add = collector.queue.add; let saved;
    collector.queue.add = spec => { saved = spec; return 'retained-fixture'; };
    try { await collector.download({ inspectionId: oldReceipt, itemIndex: 0, formatId: format }); }
    finally { collector.queue.add = add; }
    check('downloading a retained result uses its original URL and browser video data',
      saved.url === oldResult.items[0].url && saved.context.videoId === oldSnapshot.context.videoId && saved.context.detail.aweme_id === initial.aweme.detail.aweme_id);
    await ui("document.getElementById('parse').click()");
    await until(() => !!tab.state.result && tab.state.inspectionId !== oldReceipt, 'manual parse did not update the snapshot');
    check('the manual parse button updates to the current video and clears the retained-result notice',
      tab.state.result.items[0].title === '手动更新后的视频 B' && collector.resultMatchesPage(tab) && await ui("document.getElementById('retained-result-note').hidden"));
    await assert.rejects(() => collector.download({ inspectionId: oldReceipt, itemIndex: 0, formatId: format }), /页面已切换/);
    assert.throws(() => collector.asset({ inspectionId: oldReceipt, itemIndex: 0 }, 'preview'), /页面已切换/);
    check('replaced snapshots can no longer trigger previews or downloads', true);
    await saveAutomatic(true);
    await move('6234567890123456789', '自动跟随的视频 C');
    check('automatic mode still clears the previous snapshot on a content change', !tab.state.result && !collector.cache.has(oldReceipt));
    await until(() => tab.state.result?.items[0]?.title === '自动跟随的视频 C', 'automatic mode did not follow the next video');
    check('automatic mode parses the next short video without a manual click', true);
    await move('7234567890123456789', '关闭定时解析后的视频 D');
    await saveAutomatic(false); await wait(1100);
    check('disabling automatic parsing cancels a pending navigation timer', !tab.state.result && !tab.state.parsing);
    await collector.inspect(tab);
    const retained = tab.state.inspectionId;
    const loaded = new Promise(resolve => web.once('dom-ready', resolve)); web.reload(); await loaded; await wait(200);
    check('manual results and preview ownership also survive a full page reload', tab.state.inspectionId === retained && collector.cache.has(retained));
    await move('7234567890123456789', '关闭定时解析后的视频 D');
    const runner = collector.withCookies, lateResult = tab.state.result; let finish, cancelled = false;
    collector.withCookies = () => ({ promise: new Promise(resolve => { finish = resolve; }), cancel() { cancelled = true; } });
    try {
      const pending = collector.inspect(tab);
      await until(() => !!finish, 'delayed extraction did not start');
      await move('8234567890123456789', '取消旧解析后的视频 E');
      finish(lateResult); await pending;
      check('swiping cancels unfinished extraction so a late result cannot replace the panel', cancelled && !tab.state.result);
    } finally { finish?.(lateResult); collector.withCookies = runner; }
  } finally {
    await saveAutomatic(settings.autoParse);
    await web.mainFrame.executeJavaScript(`window.__INITIAL_STATE__=${JSON.stringify(initial)};history.pushState({},'',${JSON.stringify(originalUrl)})`);
    await wait(120); await collector.inspect(tab);
  }
}
module.exports = { run };
