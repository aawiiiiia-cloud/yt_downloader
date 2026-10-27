'use strict';
const assert = require('node:assert/strict');
let enabled = false;
let behanceEmbedded = false;
const hydration = [];
const response = body => new Response(`<html><head><meta charset="utf-8"><title>隔离登录入口测试</title></head><body>${body}</body></html>`, { headers: { 'content-type': 'text/html; charset=utf-8' } });
function fixture(request) {
  if (!enabled) return null;
  const url = new URL(request.url), host = url.hostname;
  const form = '<h1>扫码登录 / 密码登录</h1><form onsubmit="event.preventDefault();window.submits=(window.submits||0)+1"><input type="text" placeholder="账号"><input type="password" placeholder="密码"><button type="submit">登录</button></form>';
  if (host === 'passport.bilibili.com') return response(form);
  if (host === 'passport.zcool.com.cn') return response(url.pathname === '/loginApp.do' ? '<h1>站酷通行证</h1><iframe width="500" height="500" src="https://passport.zcool.com.cn/login.do?appId=1006"></iframe>' : form);
  if (host === 'login.taobao.com') return response(url.pathname === '/member/login.jhtml' ? '<h1>淘宝登录</h1><iframe width="500" height="500" src="https://login.taobao.com/member/fixture-login-form"></iframe>' : form);
  if (host === 'auth.services.adobe.com' || host === 'auth-light.identity.adobe.com') return response('<h1>Adobe 登录</h1><form onsubmit="event.preventDefault();window.submits=(window.submits||0)+1"><label>电子邮件地址<input type="email"></label><button>继续</button></form>');
  if (host === 'www.behance.net' && behanceEmbedded) return response('<susi-sentry-light id="adobe-host" style="display:none"></susi-sentry-light><script>document.querySelector("#adobe-host").attachShadow({mode:"open"}).innerHTML=\'<iframe width="600" height="500" src="https://auth-light.identity.adobe.com/sentry/iframe/index.html"></iframe>\';</script>');
  if (host === 'www.behance.net') return response('<header><button style="display:none" type="button" class="adobeid-signin" data-signin-from="Header">登录</button><button type="button" class="adobeid-signin" data-signin-from="updated_2020_navigation_login_hamburger" onclick="location.href=\'https://auth.services.adobe.com/collector-login-fixture\'">登录</button></header><h1>Behance 登录入口</h1>');
  if (host === 'www.bilibili.com' || host === 'www.zcool.com.cn' || host === 'item.taobao.com') return response('<h1>受控作品页，登录前后保留</h1>');
  if (host === 'www.xiaohongshu.com' || host === 'www.douyin.com') {
    const xhs = host.includes('xiaohongshu');
    const modal = xhs ? 'class="login-modal login-container"' : 'id="login-panel-new"';
    if (url.pathname === '/collector-login-hydration.js') {
      const mount = xhs ? '' : url.searchParams.has('client') ? `document.querySelector('[data-open-login]').outerHTML='<div id="qNCGQxct" data-open-login>登录</div>';` : `document.querySelector('[data-open-login]').insertAdjacentHTML('afterbegin','<svg id="svg_icon_avatar" width="20" height="20"></svg>');`;
      return new Promise(resolve => hydration.push(() => resolve(new Response(`${mount}document.querySelector('[data-open-login]').addEventListener('click',()=>{window.loginOpenCount=(window.loginOpenCount||0)+1;setTimeout(()=>{document.querySelector('#mount').innerHTML=${JSON.stringify(`<div ${modal}>${form}</div>`)}},300)});`, { headers: { 'content-type': 'application/javascript' } }))));
    }
    const button = `<button data-open-login type="button" ${xhs ? 'class="login-btn"' : ''}>登录</button>`;
    return response(`<div id="${xhs ? 'side-bar' : 'douyin-header'}">${url.searchParams.has('no-trigger') ? '' : button}</div><div id="mount"></div><form onsubmit="event.preventDefault();window.submits=(window.submits||0)+1"><input type="tel"><button class="login-btn" type="submit">登录</button></form>${url.searchParams.has('no-trigger') ? '' : `<script defer src="/collector-login-hydration.js${url.searchParams.has('client') ? '?client=1' : ''}"></script>`}`);
  }
  return null;
}

async function run(collector, check, until, wait) {
  const previous = collector.tabs.activeId, before = new Set(collector.tabs.rows.map(tab => tab.id));
  const ui = code => collector.window.webContents.executeJavaScript(code);
  const urls = {
    bilibili: 'https://www.bilibili.com/video/BVloginTest1',
    xiaohongshu: 'https://www.xiaohongshu.com/explore/0123456789abcdef01234568',
    douyin: 'https://www.douyin.com/video/1234567890123456788',
    behance: 'https://www.behance.net/gallery/987654321/fixture',
    taobao: 'https://item.taobao.com/item.htm?id=123456789012',
    zcool: 'https://www.zcool.com.cn/work/ZNzQyNjY2ODg=.html',
  };
  enabled = true;
  const wasVisible = collector.window.isVisible();
  collector.window.show();
  try {
    for (const [site, url] of Object.entries(urls)) {
      const source = collector.openTab(url, { group: site });
      const modalSite = ['xiaohongshu', 'douyin'].includes(site);
      await until(async () => source.view.webContents.getURL() === url && (modalSite ?
        await source.view.webContents.mainFrame.executeJavaScript("!!document.querySelector('[data-open-login]')").catch(() => false) : !source.view.webContents.isLoadingMainFrame()), site + ' fixture not loaded');
      await until(() => ui(`!document.querySelector('#web-login').hidden && !document.querySelector('#web-login').disabled && document.querySelector('#login-state').textContent.includes(${JSON.stringify(require('./model.cjs').SITES[site].name)})`), site + ' login button unavailable');
      await ui("document.querySelector('#web-login').click()");
      if (modalSite) {
        await until(() => !!collector.webLoginRequest && hydration.length > 0, site + ' delayed hydration not captured');
        check(site + ' waits for deferred page scripts before clicking an SSR login button', await source.view.webContents.mainFrame.executeJavaScript('!(window.loginOpenCount>0)'));
        hydration.splice(0).forEach(release => release());
      }
      await until(() => !collector.webLoginRequest && (collector.tabs.activeId !== source.id || ['xiaohongshu', 'douyin', 'behance'].includes(site)), site + ' login opening did not finish');
      await until(async () => {
        const web = collector.browser.webContents;
        const rows = await Promise.all(web.mainFrame.framesInSubtree.map(frame => frame.executeJavaScript(require('./web-login.cjs').surfaceScript(site)).catch(() => null)));
        return rows.some(row => row?.ready);
      }, site + ' actual login surface missing');
      check(site + ' right login button opens a visible left login form/modal', collector.tabs.active.group === site && !collector.state.openingWebLogin);
      check(site + ' login opening preserves the original content page', source.view.webContents.getURL() === url);
      const count = collector.tabs.rows.length, activeId = collector.tabs.activeId;
      await until(() => ui("!document.querySelector('#web-login').disabled"), site + ' login control stayed busy');
      await ui("document.querySelector('#web-login').click()");
      await wait(350);
      check(site + ' repeat login reuses its page/modal without submitting credentials', collector.tabs.activeId === activeId && collector.tabs.rows.length === count &&
        await collector.browser.webContents.mainFrame.executeJavaScript('!(window.submits>0) && !(window.loginOpenCount>1)'));
      // Remove only tabs created by this check, keeping the original smoke state.
      collector.activateTab(previous);
      for (const tab of [...collector.tabs.rows]) if (!before.has(tab.id)) collector.closeTab(tab.id);
    }
    const client = collector.openTab('https://www.douyin.com/jingxuan?client=1', { group: 'douyin' });
    await until(() => hydration.length > 0, 'Douyin client navigation hydration missing');
    const clientOpening = collector.openWebLogin('douyin');
    hydration.splice(0).forEach(release => release()); await clientOpening;
    check('Douyin client-style circular login navigation opens its modal after hydration',
      await client.view.webContents.mainFrame.executeJavaScript('document.querySelector("#qNCGQxct") && window.loginOpenCount===1'));
    for (const site of ['youtube', 'vimeo']) {
      collector.switchSite(site);
      await until(() => ui("document.querySelector('#web-login').hidden && !document.querySelector('#edge-login').hidden"), site + ' external-only login UI changed');
      await assert.rejects(collector.invoke('web-login', { site }), /Edge/);
      check(site + ' keeps only Edge login and cookie import', true);
    }
    behanceEmbedded = true;
    const embedded = collector.openTab('https://www.behance.net/login', { group: 'behance' });
    await until(() => !embedded.view.webContents.isLoadingMainFrame(), 'Adobe shadow-root fixture did not load');
    check('a hidden Adobe background iframe does not count as an open login form',
      (await embedded.view.webContents.mainFrame.executeJavaScript(require('./web-login.cjs').surfaceScript('behance'))).frames.length === 0);
    await embedded.view.webContents.mainFrame.executeJavaScript('document.querySelector("#adobe-host").style.display="block"');
    await collector.openWebLogin('behance');
    check('a visible Adobe form inside a shadow-root iframe is recognized without clicking submit',
      collector.tabs.activeId === embedded.id && !collector.state.openingWebLogin);
    await embedded.view.webContents.mainFrame.executeJavaScript(`document.querySelector('#adobe-host').shadowRoot.innerHTML='<h1>登录</h1><label>电子邮件地址<input type="text"></label><button>继续</button>'`);
    await collector.openWebLogin('behance');
    check('the current Adobe inline shadow-root email form is recognized directly', !collector.state.openingWebLogin);
    behanceEmbedded = false;
    const tab = collector.openTab('https://www.xiaohongshu.com/explore?no-trigger=1', { group: 'xiaohongshu' });
    await until(() => !tab.view.webContents.isLoadingMainFrame(), 'negative login fixture not loaded');
    const pending = collector.openWebLogin('xiaohongshu');
    await assert.rejects(collector.login('xiaohongshu'), /已有登录操作/);
    check('Edge login cannot race with embedded login opening', true);
    collector.activateTab(previous);
    await assert.rejects(pending, { cancelled: true });
    check('switching away cancels pending login without submitting a form', !collector.state.openingWebLogin && await tab.view.webContents.mainFrame.executeJavaScript('!(window.submits>0)'));
  } finally {
    hydration.splice(0).forEach(release => release());
    collector.activateTab(previous);
    for (const tab of [...collector.tabs.rows]) if (!before.has(tab.id)) collector.closeTab(tab.id);
    enabled = false;
    behanceEmbedded = false;
    if (!wasVisible) collector.window.hide();
  }
}
module.exports = { fixture, run };
