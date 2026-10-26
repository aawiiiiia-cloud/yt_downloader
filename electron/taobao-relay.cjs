'use strict';
const { navigationUrl, siteForUrl } = require('./model.cjs');

// Restrict cleanup to the disposable login callback, never a login/challenge page.
function isTaobaoRelayUrl(value) {
  try {
    const url = new URL(navigationUrl(value));
    return siteForUrl(url.href) === 'taobao' && /\/_____tmd_____\/page\/login_jump\/?$/i.test(url.pathname);
  } catch { return false; }
}

function emptyRelayDocument() {
  if (document.readyState !== 'complete' || !document.body || document.body.innerText.trim()) return false;
  const elements = [document.body, ...document.body.querySelectorAll('*')];
  if (elements.length > 3000) return false;
  for (const element of elements) {
    const style = getComputedStyle(element), rect = element.getBoundingClientRect();
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0 || !rect.width || !rect.height) continue;
    if (/^(IMG|VIDEO|AUDIO|IFRAME|CANVAS|SVG|INPUT|BUTTON|SELECT|TEXTAREA|OBJECT|EMBED)$/.test(element.tagName) ||
      element.tagName.includes('-') || element.hasAttribute('contenteditable') || element.hasAttribute('role') ||
      style.backgroundImage !== 'none') return false;
    for (const pseudo of ['::before', '::after']) {
      const content = getComputedStyle(element, pseudo).content;
      if (content && !['none', 'normal', '""', "''"].includes(content)) return false;
    }
  }
  return true;
}
const emptyRelayScript = `(${emptyRelayDocument.toString()})()`;

class TaobaoRelayGuard {
  constructor(web, onEmpty, { delay = 6000, settle = 750 } = {}) {
    this.web = web; this.onEmpty = onEmpty; this.delay = delay; this.settle = settle; this.sequence = 0;
    this.start = (_event, _url, _inPlace, main) => { if (main) this.cancel(); };
    this.ready = () => this.schedule(); this.destroy = () => this.dispose();
    web.on('did-start-navigation', this.start);
    web.on('dom-ready', this.ready); web.on('did-finish-load', this.ready); web.once('destroyed', this.destroy);
  }
  cancel() { this.sequence++; clearTimeout(this.timer); }
  schedule() {
    this.cancel();
    if (this.disposed || this.web.isDestroyed() || !isTaobaoRelayUrl(this.web.getURL())) return;
    const sequence = this.sequence, url = this.web.getURL();
    this.timer = setTimeout(() => this.probe(sequence, url, false), this.delay);
  }
  current(sequence, url) {
    return !this.disposed && sequence === this.sequence && !this.web.isDestroyed() &&
      this.web.getURL() === url && !this.web.isLoadingMainFrame();
  }
  async probe(sequence, url, confirmed) {
    if (!this.current(sequence, url)) return;
    try {
      const empty = await this.web.mainFrame.executeJavaScript(emptyRelayScript);
      if (!empty || !this.current(sequence, url)) return;
      if (confirmed) await this.onEmpty(url);
      else this.timer = setTimeout(() => this.probe(sequence, url, true), this.settle);
    } catch {} // A navigation, missing frame or uncertain document must remain untouched.
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true; this.cancel();
    this.web.removeListener('did-start-navigation', this.start);
    this.web.removeListener('dom-ready', this.ready); this.web.removeListener('did-finish-load', this.ready);
    this.web.removeListener('destroyed', this.destroy);
  }
}

// A standalone/same-tab relay has no safe owner to return to. Offer an explicit
// action instead of automatically replaying a request or resubmitting a form.
function relayHelpScript(canGoBack, url) {
  return `(() => {
    if (location.href !== ${JSON.stringify(url)}) return false;
    if (!(${emptyRelayDocument.toString()})()) return false;
    document.title = '淘宝登录中转未完成';
    const panel = document.createElement('main');
    panel.style.cssText = 'margin:64px auto;padding:28px;max-width:540px;font:15px/1.8 sans-serif;color:#383838;background:#f5f5f5;border-radius:12px';
    const title = document.createElement('h2'); title.textContent = '淘宝登录中转未完成';
    const note = document.createElement('p'); note.textContent = '此中转页没有返回可见内容。请返回原商品页，完成网页提示的登录或验证后再继续。';
    panel.append(title,note);
    if (${!!canGoBack}) {
      const back = document.createElement('button'); back.textContent = '返回上一页'; back.onclick = () => history.back(); panel.append(back,document.createTextNode('　'));
    }
    const home = document.createElement('a'); home.href = 'https://www.taobao.com/'; home.textContent = '打开淘宝首页'; panel.append(home);
    document.body.replaceChildren(panel); return true;
  })()`;
}
module.exports = { isTaobaoRelayUrl, emptyRelayScript, relayHelpScript, TaobaoRelayGuard };
