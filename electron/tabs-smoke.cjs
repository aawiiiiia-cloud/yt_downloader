'use strict';
const assert = require('node:assert/strict');
async function run(collector, check, until, wait) {
  const ui = code => collector.window.webContents.executeJavaScript(code);
  const loaded = tab => !tab.view.webContents.isLoadingMainFrame() && !!tab.view.webContents.getURL();
  const clickSite = async site => {
    await ui(`document.querySelector('[data-site="${site}"]').click()`);
    await until(() => collector.tabs.active.group === site, 'site group did not switch');
  };
  const xhs = collector.tabs.active, originalReceipt = collector.state.inspectionId;
  await until(() => ui('!!document.querySelector(\'[data-image="2"]\')'), 'image choices were not rendered');
  await ui('document.querySelector(\'[data-image="2"]\').checked=false; document.getElementById("resources-panel").scrollTop=130');
  const resourceScroll = await ui('document.getElementById("resources-panel").scrollTop');
  await xhs.view.webContents.executeJavaScript("window.savedPageMarker='still-here'; document.body.style.height='4000px'; window.scrollTo(0,710)");
  const leftScroll = await xhs.view.webContents.executeJavaScript('window.scrollY');
  await clickSite('youtube');
  check('switching site retains the original page view and mutes its audio', !xhs.view.getVisible() && xhs.view.webContents.isAudioMuted());
  const youtube = collector.tabs.active;
  const count = collector.tabs.rows.length;
  await until(() => youtube.view.webContents.executeJavaScript('!!window.__collectorLinksInstalled'), 'detail link handler was not installed');
  await youtube.view.webContents.executeJavaScript(`
    window.routerTriggered=false;
    document.addEventListener('click',()=>{window.routerTriggered=true});
    const a=document.createElement('a');a.href='https://www.youtube.com/watch?v=fixture-two';a.id='detail-fixture';a.textContent='视频 A';document.body.append(a);a.click();`, true);
  await until(() => collector.tabs.rows.length === count + 1 && collector.state.url.includes('fixture-two') && loaded(collector.tabs.active), 'video click did not open a group tab');
  const detail = collector.tabs.active;
  check('normal video link opens another tab in the YouTube group', detail.group === 'youtube' && detail !== youtube);
  check('new-tab routing prevents the original SPA router from replacing its page', await youtube.view.webContents.executeJavaScript('!window.routerTriggered && location.search.includes("v=fixture")'));
  check('tab strip renders the active group only', await ui(`document.querySelectorAll('[data-tab]').length === ${collector.tabs.rows.filter(tab => tab.group === 'youtube').length}`));
  await youtube.view.webContents.executeJavaScript("window.open('https://www.youtube.com/watch?v=fixture-two&tracking=duplicate','_blank')", true);
  await wait(100);
  check('opening the same video focuses its existing tab', collector.tabs.rows.length === count + 1 && collector.tabs.active === detail);
  const biliTarget = collector.tabs.rows.find(tab => tab.group === 'bilibili');
  const youtubeReceipt = youtube.state.inspectionId, youtubeUrl = youtube.view.webContents.getURL();
  await youtube.view.webContents.executeJavaScript(`location.href=${JSON.stringify(biliTarget.url)}`);
  await until(() => collector.tabs.active === biliTarget, 'cross-site navigation did not select its group');
  check('cross-site navigation opens its own group and preserves source resources', youtube.view.webContents.getURL() === youtubeUrl && youtube.state.inspectionId === youtubeReceipt);
  const bili = collector.tabs.active, biliReceipt = collector.state.inspectionId;
  check('returning to Bilibili retains the parsed part', collector.state.result.items[0].currentPart === 2 && !!biliReceipt);
  await ui('document.getElementById("format-select").value="16"; document.getElementById("audio-only").checked=true; document.getElementById("subtitles").checked=true; document.getElementById("audio-only").dispatchEvent(new Event("change",{bubbles:true}))');
  await ui('document.getElementById("new-tab").click()');
  await until(() => collector.tabs.active !== bili && loaded(collector.tabs.active), 'plus did not open a tab');
  const newBili = collector.tabs.active;
  const newBiliWeb = newBili.view.webContents;
  check('plus creates a homepage inside the current group', newBili.group === 'bilibili' && newBili.url === 'https://www.bilibili.com/');
  await ui(`document.querySelector('[data-close-tab="${newBili.id}"]').click()`);
  await until(() => collector.tabs.active === bili, 'closing active tab did not choose neighbor');
  await until(() => newBiliWeb.isDestroyed(), 'closed tab did not release its native page');
  check('closing a tab destroys its native page', newBili.closed && newBiliWeb.isDestroyed());
  check('video choices survive a tab round trip', collector.state.inspectionId === biliReceipt && await ui('document.getElementById("format-select").value==="16" && document.getElementById("audio-only").checked && document.getElementById("subtitles").checked && document.getElementById("format-select").disabled'));
  const detailWeb = detail.view.webContents;
  collector.closeTab(detail.id);
  await until(() => detailWeb.isDestroyed(), 'background tab did not release its native page');
  check('closing a background tab leaves the foreground page intact', collector.tabs.active === bili && detailWeb.isDestroyed());
  await clickSite('xiaohongshu');
  check('site switch restores its last selected tab and resource receipt', collector.tabs.active === xhs && collector.state.inspectionId === originalReceipt);
  check('image checkboxes and right-panel position survive switching sites', await ui(`document.querySelector('[data-image="1"]').checked && !document.querySelector('[data-image="2"]').checked && document.getElementById('resources-panel').scrollTop === ${resourceScroll}`));
  check('the original document and left-page scroll position survive switching sites', await xhs.view.webContents.executeJavaScript(`window.savedPageMarker==='still-here' && window.scrollY === ${leftScroll}`));

  const xhsCount = collector.tabs.rows.length, feedUrl = xhs.url;
  const noteUrl = 'https://www.xiaohongshu.com/explore/0123456789abcdef01234567?collector_overlay=1';
  await xhs.view.webContents.executeJavaScript(`(() => {
    const a=document.createElement('a');a.href=${JSON.stringify(noteUrl)};a.target='_blank';a.id='xhs-overlay-link';
    a.addEventListener('click',event=>{event.preventDefault();history.pushState({},'',a.href);const modal=document.createElement('div');modal.id='xhs-note-overlay';modal.textContent='本地帖子弹层';document.body.append(modal);});
    document.body.append(a);a.click();
  })()`, true);
  await until(()=>xhs.url===noteUrl,'Xiaohongshu native overlay router did not run');
  check('a normal Xiaohongshu note click reaches its overlay without creating or replacing a tab',
    collector.tabs.active===xhs&&collector.tabs.rows.length===xhsCount&&await xhs.view.webContents.executeJavaScript("!!document.getElementById('xhs-note-overlay')&&window.savedPageMarker==='still-here'"));
  check('manual parsing preserves its previous resources while an XHS overlay is open',collector.state.inspectionId===originalReceipt);
  await xhs.view.webContents.executeJavaScript("document.getElementById('xhs-note-overlay').remove();document.getElementById('xhs-overlay-link').remove();history.back()");
  await until(()=>xhs.url===feedUrl,'closing the note overlay did not return to its feed');
  check('closing an XHS overlay keeps the feed document and scroll position',await xhs.view.webContents.executeJavaScript(`window.savedPageMarker==='still-here'&&window.scrollY===${leftScroll}`));

  await ui('document.getElementById("new-tab").click()');
  await until(()=>collector.tabs.active!==xhs&&loaded(collector.tabs.active),'manual XHS plus did not open');
  const extraXhs=collector.tabs.active, extraWeb=extraXhs.view.webContents;
  check('manual plus still creates an independent Xiaohongshu homepage',extraXhs.group==='xiaohongshu'&&extraXhs.url==='https://www.xiaohongshu.com/explore');
  const toolbar=await ui('(()=>{const r=document.querySelector(".browser-tabbar").getBoundingClientRect();return{x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)}})()');
  require('node:fs').writeFileSync(require('node:path').join(__dirname,'..','electron-artifacts','new-tab-style.png'),(await collector.window.webContents.capturePage(toolbar)).toPNG());
  await until(()=>extraWeb.executeJavaScript('!!window.__collectorLinksInstalled'),'fallback link handler not ready');
  await extraWeb.executeJavaScript(`(()=>{const a=document.createElement('a');a.href=${JSON.stringify(noteUrl)};a.target='_self';document.body.append(a);a.click()})()`,true);
  await until(()=>extraXhs.url===noteUrl&&loaded(extraXhs),'same-tab XHS document navigation did not finish');
  check('XHS document navigation without a SPA router stays in its original native view',collector.tabs.active===extraXhs&&collector.tabs.rows.length===xhsCount+1&&extraXhs.view.webContents===extraWeb);
  collector.closeTab(extraXhs.id);collector.activateTab(xhs.id);

  // Let an inspection finish after switching away; it must update only its owner.
  const runner = collector.withCookies, result = collector.state.result;
  let complete;
  collector.withCookies = () => ({ promise: new Promise(resolve => { complete = resolve; }), cancel() {} });
  const pending = collector.inspect();
  await until(() => !!complete, 'background inspection did not start');
  await clickSite('bilibili'); complete(result); await pending;
  check('background inspection cannot overwrite foreground resources', collector.state.inspectionId === biliReceipt && collector.state.result.site === 'bilibili' && !collector.state.parsing);
  await clickSite('xiaohongshu');
  check('background inspection result remains available in its own tab', collector.state.result === result && collector.state.inspectionId !== originalReceipt);
  collector.withCookies = runner;

  // The queue already holds an immutable snapshot of the selected image.
  const receipt = collector.state.inspectionId;
  collector.closeTab(xhs.id);
  check('closing the source tab does not cancel its running download', collector.queue.tasks[0].status === 'running');
  await assert.rejects(() => collector.download({ inspectionId: receipt, itemIndex: 0, indices: [1] }), /页面已切换/);
  check('closed-tab receipts cannot be used for new downloads', true);
  // Persist an active XHS group for the second-process restore check.
  check('closing the last tab in a group leaves a usable homepage', collector.tabs.active.group === 'xiaohongshu' && collector.tabs.active.url === 'https://www.xiaohongshu.com/explore');
  await until(() => loaded(collector.tabs.active), 'replacement home did not load');
}
module.exports = { run };
