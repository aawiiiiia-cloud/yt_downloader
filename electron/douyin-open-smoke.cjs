'use strict';
const { webContents } = require('electron');

async function run(collector, check, until, wait) {
  const tab = collector.tabs.active, web = tab.view.webContents;
  const original = web.getURL();
  const initial = await web.mainFrame.executeJavaScript('window.__INITIAL_STATE__');
  const detail = initial.aweme.detail;
  const first = '2234567890123456789', second = '3234567890123456789';
  let child, navigations = 0;
  const navigated = () => navigations++;
  const ids = () => webContents.getAllWebContents().map(value => value.id).sort((a,b) => a-b).join(',');
  const spa = async url => { await web.mainFrame.executeJavaScript(`history.pushState({}, '', ${JSON.stringify(url)})`); await wait(100); };
  try {
    await spa('https://www.douyin.com/jingxuan');
    tab.douyinPage.remember({ aweme_list: [
      { ...detail, aweme_id: first, desc: '列表预加载视频 A' },
      { ...detail, aweme_id: second, desc: '列表预加载视频 B' },
    ] });
    await spa('https://www.douyin.com/jingxuan?modal_id=' + first);
    await collector.inspect(tab);
    check('a prefetched feed video parses on its first SPA open without a reload', collector.state.result?.items[0]?.title === '列表预加载视频 A');
    child = collector.openTab('https://www.douyin.com/jingxuan?modal_id=' + second, { group: 'douyin' });
    await until(() => !child.view.webContents.isLoadingMainFrame(), 'prefetched child tab did not load');
    await collector.inspect(child);
    check('a new group tab can use only its matching video from the source feed', collector.state.result?.items[0]?.title === '列表预加载视频 B');
    collector.closeTab(child.id); child = null; collector.activateTab(tab.id);
    await spa(original);
    collector.tabs.rows.forEach(row => row.douyinPage?.reset());
    await web.mainFrame.executeJavaScript('delete window.__INITIAL_STATE__');
    const before = ids(); web.on('did-navigate', navigated);
    await collector.inspect(tab);
    check('missing client data is supplemented through a normal browser load without reloading the visible page',
      collector.state.result?.items[0]?.title === detail.desc && web.getURL() === original && navigations === 0);
    await until(() => ids() === before, 'supplement view was not released');
    check('the background supplement releases its temporary browser view', true);
    await spa('https://www.douyin.com/jingxuan?modal_id=4234567890123456789');
    const pending = collector.inspect(tab);
    await until(() => ids() !== before, 'cancellable supplement did not start');
    collector.invalidate(tab); await pending;
    await until(() => ids() === before, 'cancelled supplement view was not released');
    check('cancelling a supplement closes its view and cannot restore stale video results', !tab.state.result && !tab.state.parsing);
  } finally {
    web.removeListener('did-navigate', navigated);
    if (child && !child.closed) collector.closeTab(child.id);
    collector.activateTab(tab.id);
    await web.mainFrame.executeJavaScript(`window.__INITIAL_STATE__=${JSON.stringify(initial)}`);
    await spa(original);
  }
  await collector.inspect(tab);
}
module.exports = { run };
