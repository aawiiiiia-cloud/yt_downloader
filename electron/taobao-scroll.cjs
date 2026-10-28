"use strict";

// Only desktop home documents need this minimum-width canvas. Product pages,
// login/verification documents and third-party frames retain their own CSS.
const HOME_MIN_WIDTH = 1200;
const HOME_SCROLL_ATTRIBUTE = 'data-media-dl-taobao-home-scroll';
function isTaobaoHome(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password && !url.port &&
      ['www.taobao.com', 'taobao.com'].includes(url.hostname) &&
      ['/', '/index.html', '/index.htm'].includes(url.pathname);
  } catch { return false; }
}

const HOME_SCROLL_CSS = `
html:root[${HOME_SCROLL_ATTRIBUTE}] {
  overflow-x: auto !important;
  overflow-y: auto !important;
  scrollbar-width: auto !important;
  scrollbar-color: #888 #ededed !important;
}
html:root[${HOME_SCROLL_ATTRIBUTE}] body {
  min-width: ${HOME_MIN_WIDTH}px !important;
  max-width: none !important;
  overflow: visible !important;
}
html:root[${HOME_SCROLL_ATTRIBUTE}]::-webkit-scrollbar {
  display: block !important;
  width: 14px !important;
  height: 14px !important;
}
html:root[${HOME_SCROLL_ATTRIBUTE}]::-webkit-scrollbar-track { background: #ededed !important; }
html:root[${HOME_SCROLL_ATTRIBUTE}]::-webkit-scrollbar-thumb {
  background: #888 !important;
  border: 3px solid #ededed !important;
  border-radius: 7px !important;
}
html:root[${HOME_SCROLL_ATTRIBUTE}]::-webkit-scrollbar-thumb:hover { background: #666 !important; }
`;

class TaobaoHomeScroll {
  constructor(web) {
    this.web = web; this.key = null; this.enabled = false; this.generation = 0; this.disposed = false;
    this.pending = Promise.resolve();
    this.onNavigation = () => { this.generation++; this.key = null; this.enabled = false; };
    this.onReady = () => this.requestSync();
    this.onInPage = (_event, _url, mainFrame) => { if (mainFrame) this.requestSync(); };
    this.onDestroyed = () => this.dispose();
    web.on('did-navigate', this.onNavigation);
    web.on('dom-ready', this.onReady);
    web.on('did-finish-load', this.onReady);
    web.on('did-navigate-in-page', this.onInPage);
    web.once('destroyed', this.onDestroyed);
    this.requestSync();
  }
  requestSync() {
    this.pending = this.pending.catch(() => {}).then(() => this.sync());
    this.pending.catch(() => {});
    return this.pending;
  }
  async setEnabled(enabled) {
    if (this.web.isDestroyed()) return false;
    // Recheck the URL inside the frame too: navigation may happen while an
    // async insertion is pending. No Node access or IPC is exposed to the page.
    return this.web.mainFrame.executeJavaScript(`(() => {
      const active = ${!!enabled} && (${isTaobaoHome.toString()})(location.href);
      document.documentElement.toggleAttribute(${JSON.stringify(HOME_SCROLL_ATTRIBUTE)}, active);
      return active;
    })()`);
  }
  async sync() {
    if (this.disposed || this.web.isDestroyed()) return;
    const generation = this.generation;
    if (!isTaobaoHome(this.web.getURL())) {
      if (this.key || this.enabled) {
        await this.setEnabled(false);
        if (generation === this.generation) this.enabled = false;
      }
      return;
    }
    if (!this.key) {
      // User-origin important rules override even author inline !important.
      // Bundled Electron cannot reliably remove user-origin sheets, so ALL
      // selectors are gated by a removable root attribute. The sheet becomes
      // inert on non-home SPA routes and disappears on document navigation.
      const key = await this.web.insertCSS(HOME_SCROLL_CSS, { cssOrigin: 'user' });
      if (this.disposed || this.web.isDestroyed() || generation !== this.generation) return;
      this.key = key;
    }
    const enabled = await this.setEnabled(true);
    if (!this.disposed && generation === this.generation) this.enabled = enabled;
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true; this.generation++;
    this.web.removeListener('did-navigate', this.onNavigation);
    this.web.removeListener('dom-ready', this.onReady);
    this.web.removeListener('did-finish-load', this.onReady);
    this.web.removeListener('did-navigate-in-page', this.onInPage);
    this.web.removeListener('destroyed', this.onDestroyed);
    this.key = null; this.enabled = false;
    // Queue deactivation after any in-flight enable so disposal always wins.
    this.pending = this.pending.catch(() => {}).then(() => this.setEnabled(false));
    this.pending.catch(() => {});
  }
}
module.exports = { HOME_MIN_WIDTH, HOME_SCROLL_ATTRIBUTE, HOME_SCROLL_CSS, isTaobaoHome, TaobaoHomeScroll };
