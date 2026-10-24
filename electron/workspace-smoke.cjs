'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { dialog } = require('electron');

async function run(collector, check, until, wait, profile) {
  const ui = code => collector.window.webContents.executeJavaScript(code);
  const site = async name => {
    await ui(`document.querySelector('[data-site="${name}"]').click()`);
    await until(() => collector.tabs.active.group === name, 'group did not switch');
  };
  await site('youtube');
  check('YouTube exposes only Edge login and cookie import', await ui("document.getElementById('web-login').hidden && [...document.querySelectorAll('.login-tools button')].filter(b=>!b.hidden).map(b=>b.id).join(',')==='edge-login,import-login'"));
  await site('bilibili');
  check('other supported sites keep all three login actions', await ui("[...document.querySelectorAll('.login-tools button')].filter(b=>!b.hidden).length===3"));
  await site('xiaohongshu');
  const previousCookies = await collector.browserSession.cookies.get({ domain: 'xiaohongshu.com' });
  const openDialog = dialog.showOpenDialog;
  let selection = { canceled: true, filePaths: [] }, called = 0, options;
  dialog.showOpenDialog = async (_window, config) => { called++; options = config; return selection; };
  try {
    await ui("document.getElementById('import-login').click()");
    await until(() => called === 1 && !collector.state.importingCookies, 'file chooser did not finish');
    check('cookie import button opens a site-specific file chooser', options.properties.includes('openFile') && options.title.includes('小红书') && options.filters[0].extensions.includes('txt'));
    check('cancelling the cookie chooser leaves the existing login intact', JSON.stringify(await collector.browserSession.cookies.get({ domain: 'xiaohongshu.com' })) === JSON.stringify(previousCookies));
    const invalid = path.join(profile, 'invalid-cookies.txt'); fs.writeFileSync(invalid, 'This is not a cookies file.');
    selection = { canceled: false, filePaths: [invalid] };
    await assert.rejects(() => collector.invoke('import-cookies', { site: 'xiaohongshu' }), /Netscape/);
    check('invalid cookie files fail before changing session cookies', JSON.stringify(await collector.browserSession.cookies.get({ domain: 'xiaohongshu.com' })) === JSON.stringify(previousCookies) && !collector.state.importingCookies);
    const expiry = Math.floor(Date.now() / 1000) + 3600;
    const unrelated = `.bilibili.com\tTRUE\t/\tTRUE\t${expiry}\tSESSDATA\tcontrolled-unrelated-cookie`;
    fs.writeFileSync(invalid, '# Netscape HTTP Cookie File\n' + unrelated + '\n');
    await assert.rejects(() => collector.invoke('import-cookies', { site: 'xiaohongshu' }), /本站点/);
    check('files containing only a different site cannot replace this site login', !(await collector.browserSession.cookies.get({ domain: 'bilibili.com' })).some(row => row.value === 'controlled-unrelated-cookie'));
    const sample = path.join(profile, 'sample-cookies.txt');
    const text = `# Netscape HTTP Cookie File\n#HttpOnly_.xiaohongshu.com\tTRUE\t/\tTRUE\t${expiry}\tweb_session\tcontrolled-import-cookie\n${unrelated}\n`;
    fs.writeFileSync(sample, text);
    selection = { canceled: false, filePaths: [sample] };
    check('selected Netscape file imports only the requested site', await collector.invoke('import-cookies', { site: 'xiaohongshu' }) === 1);
    check('cookie import preserves HttpOnly flags and source file bytes',
      (await collector.browserSession.cookies.get({ domain: 'xiaohongshu.com' })).some(row => row.name === 'web_session' && row.httpOnly && row.value === 'controlled-import-cookie') && fs.readFileSync(sample, 'utf8') === text);
    fs.writeFileSync(sample, `# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t${expiry}\tSID\tcontrolled-anonymous-cookie\n`);
    await assert.rejects(() => collector.invoke('import-cookies', { site: 'youtube' }), { code: 'YOUTUBE_AUTH_REQUIRED' });
    check('imported YouTube cookies still require server-confirmed login', collector.youtubeAuth.status !== 'verified' && !collector.state.importingCookies);
    await collector.browserSession.cookies.remove('https://www.youtube.com', 'SID'); await collector.updateLogin();
  } finally { dialog.showOpenDialog = openDialog; }

  check('removing decorative bars returns height to the workspace', await ui("!document.querySelector('.brandbar,footer') && document.querySelector('.toolbar').getBoundingClientRect().top===0 && document.getElementById('workspace').getBoundingClientRect().bottom===innerHeight"));
  collector.window.show(); collector.window.focus(); collector.window.webContents.focus(); await wait(100);
  const dividerRect = () => ui("(() => {const r=document.getElementById('pane-divider').getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+30)}})()");
  const width = () => ui("Math.round(document.querySelector('aside').getBoundingClientRect().width)");
  const before = await width(), pageUrl = collector.browser.webContents.getURL();
  let point = await dividerRect();
  collector.window.webContents.sendInputEvent({ type: 'mouseDown', button: 'left', clickCount: 1, ...point });
  await until(() => collector.paneResizing, 'divider drag did not start');
  collector.browser.webContents.sendInputEvent({ type: 'mouseMove', x: collector.browser.getBounds().width - 80, y: 60, modifiers: ['leftButtonDown'] });
  await until(async () => await width() > before + 30, 'dragging over the native webpage did not resize the panel');
  collector.browser.webContents.sendInputEvent({ type: 'mouseUp', button: 'left', clickCount: 1, x: collector.browser.getBounds().width - 1, y: 60 });
  await until(async () => !collector.paneResizing && collector.settings.panelWidth === await width(), 'divider drag did not stop or persist');
  check('divider drag continues over native web content and saves the width', collector.settings.panelWidth > before);
  await until(async () => {
    const r = await ui("(() => {const r=document.getElementById('browser-slot').getBoundingClientRect();return {x:Math.ceil(r.x),width:Math.floor(r.width)}})()");
    return collector.browser.getBounds().x === r.x && collector.browser.getBounds().width === r.width;
  }, 'native browser did not follow the divider');
  check('resizing preserves the webpage and independent download queue', collector.browser.webContents.getURL() === pageUrl && collector.queue.tasks[0].status === 'running');
  const original = await width(); point = await dividerRect();
  collector.window.webContents.sendInputEvent({ type: 'mouseDown', button: 'left', clickCount: 1, ...point });
  await until(() => collector.paneResizing, 'second drag did not start');
  collector.browser.webContents.sendInputEvent({ type: 'mouseMove', x: 120, y: 60, modifiers: ['leftButtonDown'] });
  await until(async () => await width() !== original, 'second drag did not move');
  await ui("document.getElementById('pane-divider').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");
  await until(async () => !collector.paneResizing && await width() === original, 'Escape did not restore the prior width');
  check('Escape cancels a divider drag without changing the saved width', collector.settings.panelWidth === original);
  collector.window.setSize(1060, 700); await wait(150);
  await ui("document.getElementById('pane-divider').dispatchEvent(new KeyboardEvent('keydown',{key:'End',bubbles:true}))");
  await until(async () => collector.settings.panelWidth === await width(), 'keyboard width did not save');
  check('divider limits preserve usable pane widths at the minimum window size', await ui("document.querySelector('.browser-area').getBoundingClientRect().width>=400 && document.querySelector('aside').getBoundingClientRect().width>=330 && document.querySelector('aside').getBoundingClientRect().right<=innerWidth"));
  await ui("document.getElementById('pane-divider').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))");
  await until(() => collector.settings.panelWidth === 390, 'double-click did not restore default');
  check('double-click restores the default split', await width() === 390);
  await ui("for(let i=0;i<3;i++)document.getElementById('pane-divider').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}))");
  await until(async () => collector.settings.panelWidth === 450 && await width() === 450, 'keyboard resize did not persist');
  check('keyboard resizing preserves its final value across rapid updates', true);
}
module.exports = { run };
