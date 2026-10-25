'use strict';
const assert = require('node:assert/strict');

// Called before the general HTTPS fixture. All pages are returned locally;
// this never contacts Taobao, signs a request or uses a real account.
async function fixture(request) {
  const url = new URL(request.url);
  if (!['detail.tmall.com','login.taobao.com'].includes(url.hostname) || !url.searchParams.has('collector_popup')) return null;
  const mode = url.searchParams.get('collector_popup');
  const headers = {'content-type':'text/html; charset=utf-8'};
  if (mode === 'opener') return new Response('<!doctype html><title>本地弹窗测试</title><h1>本地弹窗测试</h1><script>window.replies=[];addEventListener("message",e=>{if(e.origin===location.origin||e.origin==="https://login.taobao.com")replies.push(e.data);});</script>',{headers});
  if (mode === 'redirect') return Response.redirect('https://detail.tmall.com/wow/home/_____tmd_____/page/login_jump?collector_popup=relay',302);
  const body = request.method === 'POST' ? await request.text() : '';
  const payload = JSON.stringify({kind:mode,method:request.method,body});
  const script = `function finish(){if(window.opener)window.opener.postMessage({...${payload},referrer:document.referrer,opener:!!window.opener,noNode:typeof require==='undefined',noBridge:typeof window.collector==='undefined'},'*');window.close();}`;
  return new Response(`<!doctype html><title>本地登录 / 验证测试</title><h1>本地登录 / 验证测试</h1><button onclick="finish()">完成</button><script>${script}${mode==='wait'?'':'finish();'}</script>`,{headers});
}

async function run(collector, check, until, wait) {
  const previous = collector.tabs.active;
  const source = collector.openTab('https://detail.tmall.com/collector-popup?collector_popup=opener',{group:'taobao'});
  const web = source.view.webContents, created = new Set([source]);
  const relay = 'https://detail.tmall.com/wow/home/_____tmd_____/page/login_jump?collector_popup=relay';
  const pending = 'https://login.taobao.com/member/login.jhtml?collector_popup=wait';
  const exec = code => web.executeJavaScript(code,true);
  const reply = kind => exec(`window.replies.some(r=>r.kind===${JSON.stringify(kind)}&&r.opener&&r.noNode&&r.noBridge)`);
  try {
    await until(()=>!web.isLoadingMainFrame()&&web.getURL().includes('collector_popup=opener'),'popup source did not load');
    const count = collector.tabs.rows.length;
    check('a login relay retains a native window handle',await exec(`window.open(${JSON.stringify(relay)},'collector-relay')!==null`));
    await until(()=>reply('relay'),'login relay did not notify its opener');
    await until(()=>collector.popups.size===0,'login relay did not close itself');
    check('a completed login relay leaves no blank tab and preserves the original page',collector.tabs.rows.length===count&&collector.tabs.active===source&&web.getURL().includes('collector_popup=opener'));

    await exec(`void window.open(${JSON.stringify(pending)},'collector-verification')`);
    await until(()=>collector.popups.size===1,'verification window was not retained');
    const popup=[...collector.popups.keys()][0], popupWeb=popup.webContents;
    await until(()=>!popupWeb.isLoadingMainFrame()&&popupWeb.getURL()===pending,'verification fixture did not load');
    check('an interactive login or verification page remains available in the same session',
      popup.getParentWindow()===collector.window&&popupWeb.session===collector.browserSession&&await popupWeb.executeJavaScript('!!window.opener && !!document.querySelector("button")'));
    const prefs=popupWeb.getLastWebPreferences();
    check('native login children retain sandbox and isolation with no local preload',prefs.sandbox&&prefs.contextIsolation&&prefs.webSecurity&&!prefs.nodeIntegration&&!prefs.preload);
    await exec(`void window.open(${JSON.stringify(pending)},'collector-verification')`); await wait(100);
    check('a named login window is reused by Chromium',collector.popups.size===1&&[...collector.popups.keys()][0]===popup);
    check('temporary login URLs are absent from saved tab metadata',!JSON.stringify(collector.tabs.serialize()).includes('collector_popup=wait'));
    collector.activateTab(previous.id);
    await popupWeb.executeJavaScript('setTimeout(finish, 30); void 0');
    await until(()=>popup.isDestroyed()&&collector.popups.size===0,'verification window did not finish');
    check('a background login callback does not switch the current site or overwrite its resources',collector.tabs.active===previous);
    collector.activateTab(source.id);

    check('an initially blank login popup remains writable by its opener',await exec(`(()=>{const child=window.open('about:blank','collector-blank');if(!child)return false;child.location.href=${JSON.stringify(relay.replace('=relay','=blank'))};return true;})()`));
    await until(()=>reply('blank'),'initially blank child lost its opener');
    await until(()=>collector.popups.size===0,'initially blank relay did not close');

    await exec(`(()=>{const form=document.createElement('form');form.method='POST';form.action='https://login.taobao.com/member/login.jhtml?collector_popup=post';form.target='collector-login-post';const input=document.createElement('input');input.name='fixture';input.value='local-only';form.append(input);document.body.append(form);form.submit();form.remove();})()`);
    await until(()=>reply('post'),'POST login relay did not notify the opener');
    await until(()=>collector.popups.size===0,'POST login relay did not close');
    const postReply=await exec("window.replies.find(r=>r.kind==='post')");
    assert.equal(postReply.method,'POST'); assert.equal(postReply.body,'fixture=local-only');
    assert.equal(postReply.referrer,'https://detail.tmall.com/');
    check('a login form preserves POST data and the browser referrer',true);

    const product='https://detail.tmall.com/item.htm?id=424242';
    check('a product popup retains its native handle while opening in its group',await exec(`window.open(${JSON.stringify(product)},'_blank')!==null`));
    await until(()=>collector.tabs.active!==source&&collector.tabs.active.url===product&&!collector.browser.webContents.isLoadingMainFrame(),'native product tab did not load');
    const productTab=collector.tabs.active;created.add(productTab);
    check('a grouped product tab retains its opener and safe web preferences',await productTab.view.webContents.executeJavaScript('!!window.opener && typeof require===\"undefined\" && typeof window.collector===\"undefined\"'));
    await exec(`window.open(${JSON.stringify(product+'&tracking=duplicate')},'_blank')`);await wait(100);
    check('duplicate product links still focus the existing tab',collector.tabs.rows.length===count+1&&collector.tabs.active===productTab);
    await productTab.view.webContents.mainFrame.executeJavaScript('setTimeout(()=>window.close(),30); void 0');
    await until(()=>productTab.closed&&!collector.tabs.rows.includes(productTab),'script-closed product tab left a stale tab');
    check('closing a native child removes its tab without closing the main window',!collector.window.isDestroyed()&&collector.tabs.active===source);

    await exec(`void window.open('https://detail.tmall.com/item.htm?id=424243&collector_popup=redirect','_blank')`);
    await until(()=>reply('relay'),'a product redirected to login lost its opener');
    await until(()=>collector.tabs.rows.length===count&&collector.popups.size===0,'redirected login left a blank product tab');
    check('a product redirect can finish its login relay and close its grouped tab',!collector.window.isDestroyed()&&collector.tabs.active===source);

    const before=collector.tabs.rows.length;
    await exec("void window.open('file:///C:/collector-not-a-real-file','_blank')");await wait(100);
    check('local-file popup requests stay blocked and explicit executable URLs are rejected',collector.tabs.rows.length===before&&collector.popups.size===0&&
      collector.handleWindowOpen(source,web,{url:'javascript:void(0)'}).action==='deny');

    await exec(`void window.open(${JSON.stringify(pending)},'collector-owner-close')`);
    await until(()=>collector.popups.size===1,'owned popup did not open');
    const owned=[...collector.popups.keys()][0];
    collector.closeTab(source.id);
    await until(()=>owned.isDestroyed()&&collector.popups.size===0,'closing the owner leaked a login window');
    check('closing the source tab releases its temporary login window',source.closed);
    assert.ok(!collector.window.isDestroyed());
  } finally {
    for(const tab of created)if(!tab.closed)collector.closeTab(tab.id);
    if(!previous.closed)collector.activateTab(previous.id);
  }
}
module.exports={fixture,run};
