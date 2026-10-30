'use strict';
const { app, BrowserWindow, WebContentsView, ipcMain, dialog, shell, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { randomUUID } = require('node:crypto');
const { pathToFileURL } = require('node:url');
const { SITES, navigationUrl, siteForUrl, contentKey, cookieText, readCookies, hasAuth, concurrencySettings, DownloadQueue } = require('./model.cjs');
const { pythonTask } = require('./bridge.cjs');
const { Assets, readBody } = require('./assets.cjs');
const { YouTubeAuth, loginEvidence, fingerprint, authRequired } = require('./youtube-auth.cjs');
const { SiteTabs, linkScript, MAX_TABS } = require('./site-tabs.cjs');
const { taobaoPopupKind } = require('./browser-popups.cjs');
const { TaobaoRelayGuard, relayHelpScript, isTaobaoRelayUrl } = require('./taobao-relay.cjs');
const { DouyinPage, snapshotScript: douyinSnapshotScript } = require('./douyin-page.cjs');
const { resolveDouyinContext } = require('./douyin-context.cjs');
const { YouTubeProvider } = require('./youtube-provider.cjs');
const { LoginState } = require('./login-state.cjs');
const { openFolder } = require('./task-history.cjs');
const { snapshotScript: behanceSnapshotScript } = require('./behance-page.cjs');
const { externalEntries, collectVimeo } = require('./external-media.cjs');
const { snapshotScript: taobaoSnapshotScript } = require('./taobao-page.cjs');
const { snapshotScript: zcoolSnapshotScript } = require('./zcool-page.cjs');
const { classifyFailure, safeDetail } = require('./task-failure.cjs');
const { loginPlan, loginHost, surfaceScript, waitForLogin } = require('./web-login.cjs');
const { TaobaoHomeScroll } = require('./taobao-scroll.cjs');
const { waitForPreparation } = require('./task-preflight.cjs');

const smoke = process.argv.includes('--collector-smoke');
const liveCheck = process.argv.includes('--collector-live-check');
const persistenceCheck = process.argv.includes('--collector-persistence-check');
const profile = smoke || liveCheck ? path.join(__dirname, '..', 'electron-artifacts', `${smoke ? 'smoke' : 'live'}-${Date.now()}`) :
  process.env.YT_COLLECTOR_HOME || path.join(os.homedir(), '.media-collector');
app.setName('media-dl');
app.setPath('userData', profile);
if (liveCheck) {
  require('electron').crashReporter.start({ productName: 'media-dl', uploadToServer: false });
  process.on('uncaughtException', error => { console.error('MAIN JS:', error); app.exit(1); });
  process.on('unhandledRejection', error => { console.error('MAIN PROMISE:', error); });
  app.on('child-process-gone', (_event, details) => console.error('CHILD EXIT:', details));
}

function readJson(file, fallback) { try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return fallback; } }
function writeJson(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file + '.tmp', JSON.stringify(data, null, 2), { mode: 0o600 }); fs.renameSync(file + '.tmp', file);
}
const snapshotScript = String.raw`(() => {
  const id = location.pathname.match(/\/(?:explore|discovery\/item|user\/profile\/[^/]+)\/([\da-f]{16,32})/i)?.[1];
  if (!id) return null;
  const state = window.__INITIAL_STATE__;
  const map = state?.note?.noteDetailMap?.value || state?.note?.noteDetailMap;
  const item = map?.[id];
  const note = item?.note?.value || item?.note;
  if (!note) return null;
  return JSON.parse(JSON.stringify({noteId: id, note: {
    title: note.title, desc: note.desc, type: note.type, user: {nickname: note.user?.nickname},
    video: note.video ? {present: true} : undefined,
    imageList: (note.imageList || []).slice(0, 200).map(i => ({
      fileId: i.fileId, traceId: i.traceId, urlDefault: i.urlDefault, urlPre: i.urlPre, width: i.width, height: i.height
    }))
  }}));
})()`;
const biliSnapshotScript = String.raw`(() => {
  const videoId = location.pathname.match(/\/video\/(BV[0-9A-Za-z]+)/)?.[1];
  const video = window.__INITIAL_STATE__?.videoData || window.__INITIAL_STATE__?.videoInfo;
  const play = window.__playinfo__?.data || window.__playinfo__;
  if (!videoId || !video || !play) return null;
  return JSON.parse(JSON.stringify({ videoId, part: Number(new URL(location.href).searchParams.get('p') || 1),
    video: {title: video.title, pic: video.pic, duration: video.duration, owner: {name: video.owner?.name},
      pages: (video.pages || []).slice(0, 200).map(p => ({page: p.page, part: p.part, duration: p.duration}))}, playInfo: play }));
})()`;

class CollectorApp {
  constructor() {
    fs.mkdirSync(path.join(profile, 'tasks'), { recursive: true });
    const legacy = smoke || liveCheck || persistenceCheck ? {} : readJson(path.join(os.homedir(), '.yt_dlp_gui.json'), {});
    const savedSettings = readJson(path.join(profile, 'settings.json'), {}) || {};
    this.settings = { saveDir: legacy.save_dir || path.join(os.homedir(), 'Downloads'), proxy: legacy.proxy || '',
      autoParse: false, imageFormat: 'original', imageQuality: 95, panelWidth: 390, ...savedSettings,
      ...concurrencySettings(savedSettings), settingsVersion: 1 };
    // Disable the former default once on upgrade, then remember explicit user choices.
    if (!savedSettings.settingsVersion) this.settings.autoParse = false;
    this.shared = { health: null, login: {}, loginExpired: {}, loginRunning: null, importingCookies: false, openingWebLogin: null };
    this.loginState = new LoginState(readJson(path.join(profile, 'login-errors.json'), {}));
    this.tabs = new SiteTabs(readJson(path.join(profile, 'tabs.json'), null));
    this.tabs.rows.forEach(tab => this.initializeTab(tab));
    this.handles = new Set(); this.cache = new Map(); this.popups = new Map(); this.closing = false;
    this.providerTimings = [];
    this.youtubeProvider = new YouTubeProvider({ track: handle => this.track(handle), onTiming: row => {
      this.providerTimings = [...this.providerTimings, { at: new Date().toISOString(), ...row }].slice(-30);
      try { writeJson(path.join(profile, 'provider-timings.json'), { timings: this.providerTimings }); } catch {}
    } });
    this.runTask = pythonTask;
    this.externalTasks = new WeakMap();
    this.youtubeAuth = new YouTubeAuth(() => this.browserSession.cookies.get({}), () => this.verifyYouTube(), () => this.publish());
    this.queue = new DownloadQueue((spec, onEvent) => this.withCookies(spec, onEvent), () => this.publish(), this.settings);
    const history = readJson(path.join(profile, 'history.json'), []);
    this.queue.tasks = (Array.isArray(history) ? history : []).filter(t => t && typeof t === 'object').slice(0, 60).map(t => {
      const interrupted = ['queued', 'running', 'cancelling'].includes(t.status);
      const task = { ...t, status: interrupted ? 'failed' : t.status };
      if (task.status === 'failed') {
        task.failure = classifyFailure(interrupted ? '上次任务已中断，请重新解析后下载' : t.failure || t.error, t.logs || []);
        task.error = task.failure.message;
      }
      return task;
    });
  }
  get state() { return this.tabs.active.state; }
  get browser() { return this.tabs.active.view; }
  get documentKey() { return this.tabs.active.documentKey; }
  initializeTab(tab) {
    if (tab.state) return tab;
    tab.state = { url: tab.url, title: tab.title, site: siteForUrl(tab.url), loading: false, parsing: false,
      result: null, inspectionId: null, error: '', logs: [] };
    for (const key of Object.keys(this.shared)) Object.defineProperty(tab.state, key, {
      enumerable: true, get: () => this.shared[key], set: value => { this.shared[key] = value; },
    });
    tab.sequence = 0; tab.assetsAbort = new AbortController(); return tab;
  }
  resultMatchesPage(tab = this.tabs.active) {
    const record = this.cache.get(tab.state.inspectionId);
    return !!record && contentKey(record.url) === contentKey(tab.state.url);
  }
  publicState() { return { ...this.state, resultMatchesPage: this.resultMatchesPage(), activeTabId: this.tabs.activeId, activeGroup: this.tabs.active.group,
    tabs: this.tabs.rows.map(tab => ({ id: tab.id, group: tab.group, title: tab.title,
      url: tab.url, loading: tab.state?.loading || false, parsing: tab.state?.parsing || false })),
    settings: this.settings, youtubeAuth: this.youtubeAuth.status,
    loginExpired: { ...this.state.loginExpired, youtube: this.state.loginExpired.youtube || this.youtubeAuth.status === 'expired' },
    tasks: this.queue.list().map(t => ({ ...t, canRetry: !!this.queue.tasks.find(row => row.id === t.id)?.spec })) }; }
  publish() {
    if (!this.window?.isDestroyed() && this.window?.webContents && !this.window.webContents.isDestroyed())
      this.window.webContents.send('collector:state', this.publicState());
    clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.persist(), 400); this.saveTimer.unref();
  }
  persist() {
    writeJson(path.join(profile, 'settings.json'), this.settings);
    writeJson(path.join(profile, 'history.json'), this.queue.list());
    writeJson(path.join(profile, 'login-errors.json'), this.loginState.invalid);
    writeJson(path.join(profile, 'tabs.json'), this.tabs.serialize());
  }
  track(handle) {
    this.handles.add(handle);
    handle.promise.then(() => this.handles.delete(handle), () => this.handles.delete(handle));
    return handle;
  }
  log(text, tab = this.tabs.active) { tab.state.logs = [...tab.state.logs, safeDetail(text)].slice(-35); this.publish(); }
  async create() {
    this.browserSession = session.fromPartition('persist:collector-web');
    this.assets = new Assets((url, options) => this.browserSession.fetch(url, { ...options,
      headers: { 'User-Agent': this.userAgent, ...options.headers } }));
    this.browserSession.setPermissionRequestHandler((_contents, permission, callback) => callback(permission === 'fullscreen'));
    this.browserSession.setPermissionCheckHandler((_contents, permission) => permission === 'fullscreen');
    await this.applyProxy();
    this.window = new BrowserWindow({ width: 1440, height: 920, minWidth: 1060, minHeight: 700,
      show: !smoke && !liveCheck && !persistenceCheck, backgroundColor: '#f4f4f4', title: 'media-dl', autoHideMenuBar: true,
      webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true },
    });
    this.browserBounds = { x: 17, y: 138, width: 1006, height: 730 };
    // Use the bundled Chromium UA for both browsing and extraction, without the app product token.
    const platform = process.platform === 'win32' ? 'Windows NT 10.0; Win64; x64' : process.platform === 'darwin' ? 'Macintosh; Intel Mac OS X 10_15_7' : 'X11; Linux x86_64';
    this.userAgent = `Mozilla/5.0 (${platform}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${process.versions.chrome} Safari/537.36`;
    this.browserSession.setUserAgent(this.userAgent);
    session.defaultSession.setUserAgent(this.userAgent);
    this.window.webContents.setUserAgent(this.userAgent);
    this.browserSession.cookies.on('changed', () => this.updateLogin().catch(() => {}));
    this.window.on('close', event => {
      if (this.allowClose) return;
      event.preventDefault(); this.close();
    });
    this.window.on('blur', () => this.endPaneResize());
    this.activateTab(this.tabs.activeId);
    await this.window.loadFile(path.join(__dirname, 'ui', 'index.html'));
    await this.updateLogin();
    // Warm the large Node provider in the background. On a cold Windows cache
    // this can take tens of seconds, but it must not block the application UI.
    this.youtubeProvider.start();
    this.healthTask = this.track(pythonTask({ command: 'health' }));
    this.healthTask.promise.then(health => { this.state.health = health; this.publish(); }, error => { this.state.error = error.message; this.publish(); });
    return this;
  }
  ensureView(tab, nativeContents) {
    this.initializeTab(tab);
    if (tab.view) return;
    const view = tab.view = new WebContentsView({ ...(nativeContents ? { webContents: nativeContents } : {}), webPreferences: { session: this.browserSession,
      contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true } });
    this.window.contentView.addChildView(view); view.setVisible(false); view.setBounds(this.browserBounds);
    const web = view.webContents;
    if (tab.group === 'taobao') tab.homeScroll = new TaobaoHomeScroll(web);
    if (tab.group === 'douyin') tab.douyinPage = new DouyinPage(web);
    web.setUserAgent(this.userAgent); web.setAudioMuted(true);
    // Native webpage views can receive the pointer while it crosses the divider.
    // Forward only an active local drag; the remote page has no resize IPC bridge.
    web.on('before-mouse-event', (event, mouse) => {
      if (!this.paneResizing) return;
      event.preventDefault();
      if (mouse.type === 'mouseMove' || mouse.type === 'mouseUp') {
        this.window.webContents.send('collector:resize-pointer', {
          type: mouse.type === 'mouseUp' ? 'end' : 'move', x: view.getBounds().x + mouse.x,
        });
        if (mouse.type === 'mouseUp') this.paneResizing = false;
      }
    });
    web.on('before-input-event', (event, input) => {
      if (this.paneResizing && input.key === 'Escape') { event.preventDefault(); this.endPaneResize(true); }
    });
    web.setWindowOpenHandler(details => this.handleWindowOpen(tab, web, details));
    tab.relayGuard = new TaobaoRelayGuard(web, url => this.recoverEmptyRelay(tab, url));
    web.once('destroyed', () => setImmediate(() => {
      if (!tab.closed && !this.closing) this.closeTab(tab.id);
    }));
    web.on('will-navigate', (event, details) => {
      try {
        const next = navigationUrl(typeof details === 'string' ? details : details.url), group = siteForUrl(next);
        const key = contentKey(next), current = contentKey(tab.state.url);
        const sameBiliVideo = current?.startsWith('bilibili:') && current.split(':')[1] === key?.split(':')[1];
        const sameXhsSite = group === 'xiaohongshu' && tab.group === group && siteForUrl(tab.state.url) === group;
        if ((group && group !== tab.group) || (key && key !== current && !sameBiliVideo && !sameXhsSite)) {
          event.preventDefault(); tab.state.loading = false;
          setImmediate(() => {
            if (this.closing || tab.closed) return;
            try { this.openTab(next, { group: group || tab.group, reuse: true }); }
            catch (error) { tab.state.error = error.message; this.publish(); }
          });
          this.publish();
        }
      } catch { event.preventDefault(); tab.state.loading = false; this.publish(); }
    });
    web.on('did-start-navigation', (_e, url, inPlace, isMainFrame) => {
      if (!isMainFrame || tab.closed) return;
      // Analytics often replace query parameters without changing the note/video.
      // Only invalidate an in-page result when its actual content identity changes.
      if (inPlace && contentKey(url) && contentKey(url) === contentKey(tab.state.url)) return;
      if (inPlace) this.invalidate(tab, { preserveResult: !this.settings.autoParse });
      tab.state.loading = true; this.publish();
    });
    // A cancelled/provisional navigation must not discard the source tab's resources.
    web.on('did-navigate', (_event, url) => { this.invalidate(tab, { preserveResult: !this.settings.autoParse }); tab.douyinPage?.reset(); tab.documentKey = contentKey(url); });
    web.on('did-navigate-in-page', (_e, url, isMainFrame) => {
      if (!isMainFrame || tab.closed) return;
      // B站的静态 __playinfo__ 可能在 SPA 分P切换后仍指向上一段。
      // 第一阶段以新内容链接重新加载，确保浏览器快照对应当前视频/分P。
      if (siteForUrl(url) === 'bilibili' && contentKey(url) && contentKey(url) !== tab.documentKey) {
        web.reload(); return;
      }
      this.pageReady(tab);
    });
    web.on('did-finish-load', () => this.pageReady(tab));
    web.on('dom-ready', () => {
      web.mainFrame.executeJavaScript(linkScript).catch(() => {}); this.pageReady(tab);
    });
    web.on('did-stop-loading', () => { if (!tab.closed) { tab.state.loading = false; this.publish(); } });
    web.on('page-title-updated', () => {
      if (!tab.closed) { tab.title = tab.state.title = web.getTitle(); this.publish(); }
    });
    web.on('did-fail-load', (_event, code, description, _url, main) => {
      if (main && code !== -3 && !tab.closed) { tab.state.loading = false; tab.state.error = '网页加载失败，请检查网络或代理设置（' + description + '）'; this.publish(); }
    });
    web.on('media-started-playing', () => { if (this.tabs.activeId !== tab.id) this.pauseTab(tab); });
    web.on('render-process-gone', () => {
      if (!tab.closed && !this.closing) { this.invalidate(tab); tab.state.loading = false;
        tab.state.error = '此标签页面已停止运行，点击刷新即可重新加载'; this.publish(); }
    });
    // Chromium already owns this child's first navigation, POST data and opener.
    if (!nativeContents) {
      const load = tab.url ? web.loadURL(tab.url) : web.loadFile(path.join(__dirname, 'ui', 'welcome.html'));
      load.catch(() => {});
    }
  }
  handleWindowOpen(tab, source, details) {
    if (this.closing || tab.closed) return { action: 'deny' };
    const { url, postBody } = details;
    const kind = taobaoPopupKind(source.getURL() === 'about:blank' ? tab.state.url : source.getURL(), url);
    let next;
    try { next = kind === 'login' && url === 'about:blank' ? url : navigationUrl(url); }
    catch { return { action: 'deny' }; }
    const existing = kind !== 'login' && !postBody && this.tabs.find(next, siteForUrl(next) || tab.group);
    if (kind && !existing) {
      if ((kind === 'tab' && this.tabs.rows.length >= MAX_TABS) || (kind === 'login' && this.popups.size >= 4)) {
        tab.state.error = kind === 'tab' ? '已打开 36 个标签，请先关闭暂时不用的页面' : '请先完成或关闭已有的登录 / 验证窗口';
        this.publish(); return { action: 'deny' };
      }
      return { action: 'allow', outlivesOpener: kind === 'tab',
        overrideBrowserWindowOptions: { show: false, webPreferences: { session: this.browserSession,
          contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true } },
        createWindow: options => {
          if (kind === 'login') return this.createLoginPopup(tab, options);
          const child = this.initializeTab(this.tabs.add('taobao', next));
          child.openerTabId = tab.id;
          this.ensureView(child, options.webContents); this.activateTab(child.id);
          return child.view.webContents;
        } };
    }
    setImmediate(() => {
      if (this.closing || tab.closed) return;
      try { this.openTab(next, { group: siteForUrl(next) || tab.group, reuse: true }); }
      catch (error) { tab.state.error = error.message; this.publish(); }
    });
    return { action: 'deny' };
  }
  createLoginPopup(tab, options) {
    const popup = new BrowserWindow({ ...options, parent: this.window, show: false,
      width: 960, height: 740, minWidth: 600, minHeight: 480, frame: true, resizable: true,
      fullscreen: false, fullscreenable: false, alwaysOnTop: false, skipTaskbar: false,
      title: '淘宝 · 登录 / 验证', autoHideMenuBar: true });
    const web = popup.webContents;
    this.popups.set(popup, tab.id); web.setUserAgent(this.userAgent);
    const relayGuard = new TaobaoRelayGuard(web, () => {
      if (!popup.isDestroyed()) popup.destroy();
    });
    web.setWindowOpenHandler(details => this.handleWindowOpen(tab, web, details));
    const restrict = (event, details) => {
      const url = typeof details === 'string' ? details : details.url;
      try { if (url !== 'about:blank') navigationUrl(url); } catch { event.preventDefault(); }
    };
    web.on('will-navigate', restrict); web.on('will-redirect', restrict);
    const show = () => { if (!popup.isDestroyed() && !this.closing && !tab.closed && !smoke && !liveCheck && !persistenceCheck) popup.show(); };
    // Quick relay windows usually close themselves before this timer fires.
    // Slow pages and real verification windows remain visible and user controlled.
    const timer = setTimeout(show, 350);
    web.on('page-title-updated', event => {
      event.preventDefault();
      let host = ''; try { host = new URL(web.getURL()).hostname; } catch {}
      popup.setTitle('淘宝 · 登录 / 验证' + (host ? ' · ' + host : ''));
    });
    popup.once('closed', () => { relayGuard.dispose(); clearTimeout(timer); this.popups.delete(popup); this.updateLogin().catch(() => {}); });
    return web;
  }
  async recoverEmptyRelay(tab, url) {
    if (tab.closed || this.closing || !isTaobaoRelayUrl(url)) return;
    const web = tab.view.webContents;
    if (web.isDestroyed() || web.getURL() !== url) return;
    const owner = this.tabs.rows.find(row => row.id === tab.openerTabId && !row.closed);
    if (owner) {
      const active = this.tabs.activeId === tab.id;
      this.closeTab(tab.id);
      if (active && !owner.closed) this.activateTab(owner.id);
      await this.updateLogin();
    } else {
      await web.mainFrame.executeJavaScript(relayHelpScript(web.navigationHistory.canGoBack(), url));
    }
  }
  pauseTab(tab) {
    const web = tab.view?.webContents;
    if (!web || web.isDestroyed()) return;
    web.setAudioMuted(true);
    web.mainFrame.executeJavaScript("document.querySelectorAll('video,audio').forEach(media => media.pause())").catch(() => {});
  }
  activateTab(id) {
    if (this.webLoginRequest && this.webLoginRequest.tab.id !== id) this.webLoginRequest.cancelled = true;
    const previous = this.tabs.active;
    const tab = this.initializeTab(this.tabs.select(id));
    if (previous && previous !== tab && previous.view) { previous.view.setVisible(false); this.pauseTab(previous); }
    this.ensureView(tab); tab.view.setBounds(this.browserBounds); tab.view.setVisible(true);
    tab.view.webContents.setAudioMuted(false); this.publish(); return tab;
  }
  switchSite(group) {
    const previous = this.tabs.active;
    const tab = this.tabs.switchGroup(group);
    // switchGroup changes the model first; hide the former view explicitly.
    if (previous !== tab && previous.view) { previous.view.setVisible(false); this.pauseTab(previous); }
    this.activateTab(tab.id);
    if (!tab.url) this.navigate(SITES[group].home);
    return tab;
  }
  openTab(url, { group = siteForUrl(url) || this.tabs.active.group, reuse = false } = {}) {
    const target = navigationUrl(url || SITES[group]?.home);
    const existing = reuse && this.tabs.find(target, group);
    return this.activateTab(existing ? existing.id : this.initializeTab(this.tabs.add(group, target)).id);
  }
  closeTab(id) {
    const tab = this.tabs.rows.find(row => row.id === id);
    if (!tab) return;
    this.disposeTab(tab); this.tabs.remove(id); this.activateTab(this.tabs.activeId);
  }
  disposeTab(tab) {
    if (tab.closed) return;
    tab.closed = true; this.invalidate(tab); tab.assetsAbort.abort(); tab.douyinPage?.dispose(); tab.relayGuard?.dispose(); tab.homeScroll?.dispose();
    for (const [popup, owner] of this.popups) if (owner === tab.id && !popup.isDestroyed()) popup.destroy();
    // A script-closed WebContentsView has already detached its native view and
    // no longer returns webContents. Do not access it as a still-live page.
    const web = tab.view?.webContents;
    if (web && !web.isDestroyed()) {
      this.window.contentView.removeChildView(tab.view);
      web.close({ waitForBeforeUnload: false });
    }
  }
  destroyViews() { this.tabs.rows.forEach(tab => this.disposeTab(tab)); }
  invalidate(tab = this.tabs.active, { preserveResult = false } = {}) {
    tab.sequence++; clearTimeout(tab.parseTimer); tab.parseTask?.cancel();
    // Browsing may cancel in-flight extraction while a completed manual snapshot stays usable.
    if (preserveResult && tab.state.result && this.cache.has(tab.state.inspectionId)) {
      Object.assign(tab.state, { parsing: false, error: '' }); return;
    }
    tab.assetsAbort.abort(); tab.assetsAbort = new AbortController();
    this.cache.delete(tab.state.inspectionId);
    Object.assign(tab.state, { result: null, inspectionId: null, parsing: false, error: '', logs: [] });
  }
  pageReady(tab) {
    if (tab.closed || this.closing) return;
    const web = tab.view.webContents, state = tab.state;
    state.url = web.getURL(); state.site = siteForUrl(state.url);
    tab.url = /^https?:/.test(state.url) ? state.url : ''; tab.title = state.title = web.getTitle(); state.loading = false;
    this.publish(); clearTimeout(tab.parseTimer);
    if (this.settings.autoParse && contentKey(state.url) && (!state.result || !this.resultMatchesPage(tab)) && !state.parsing)
      tab.parseTimer = setTimeout(() => {
        if (!this.settings.autoParse || tab.closed || this.closing) return;
        this.inspect(tab).catch(error => { state.error = error.message; this.publish(); });
      }, 850);
  }
  navigate(url) {
    if (this.webLoginRequest) this.webLoginRequest.cancelled = true;
    const target = navigationUrl(url), group = siteForUrl(target);
    if (group && group !== this.tabs.active.group) { this.openTab(target, { group, reuse: true }); return; }
    this.invalidate(this.tabs.active, { preserveResult: !this.settings.autoParse }); this.tabs.active.url = this.state.url = target;
    this.state.site = group; this.state.loading = true; this.publish();
    this.browser.webContents.loadURL(target).catch(() => {});
  }
  layout(bounds) {
    const [width, height] = this.window.getContentSize();
    const rect = Object.fromEntries(['x', 'y', 'width', 'height'].map(key => [key, Math.round(Number(bounds[key]) || 0)]));
    if (rect.x < 0 || rect.y < 0 || rect.width < 200 || rect.height < 100 || rect.x + rect.width > width + 2 || rect.y + rect.height > height + 2) return;
    this.browserBounds = rect; this.browser.setBounds(rect);
  }
  endPaneResize(cancel = false) {
    if (!this.paneResizing) return;
    this.paneResizing = false;
    if (!this.window.isDestroyed()) this.window.webContents.send('collector:resize-pointer', { type: 'end', cancel });
  }
  async applyProxy() {
    const proxy = this.settings.proxy;
    const config = proxy ? { mode: 'fixed_servers', proxyRules: proxy.replace(/^socks5h:/i, 'socks5:'), proxyBypassRules: '<local>' } : { mode: 'system' };
    await Promise.all([this.browserSession.setProxy(config), session.defaultSession.setProxy(config)]);
  }
  async updateLogin() {
    const cookies = await this.browserSession.cookies.get({});
    const login = this.loginState.observe(cookies);
    this.state.login = login.detected; this.state.loginExpired = login.expired;
    this.youtubeAuth.observe(cookies);
    this.publish();
  }
  async rejectCredentials(site, key) {
    await this.updateLogin();
    if (!this.loginState.reject(site, key)) return;
    this.state.loginExpired = this.loginState.state().expired;
    if (site === 'youtube') { this.youtubeAuth.status = 'expired'; this.youtubeAuth.until = 0; }
    this.publish();
  }
  async ensureYouTube(force = false) {
    try {
      await this.youtubeAuth.ensure(force);
      const verifiedKey = this.youtubeAuth.key;
      await this.updateLogin();
      if (this.youtubeAuth.status !== 'verified' || this.youtubeAuth.key !== verifiedKey) throw authRequired('YouTube 登录状态已更新，请再试一次');
      this.loginState.accept('youtube', verifiedKey);
      this.state.loginExpired = this.loginState.state().expired; this.publish();
    } catch (error) {
      if (this.youtubeAuth.status === 'expired') await this.rejectCredentials('youtube', this.youtubeAuth.key);
      throw error;
    }
  }
  async cookieSnapshot(site, verified = false, snapshot = {}, signal) {
    const cookies = await this.browserSession.cookies.get({});
    if (signal?.aborted) throw Object.assign(new Error('操作已取消'), { cancelled: true });
    if (verified && (this.youtubeAuth.status !== 'verified' || fingerprint(cookies) !== this.youtubeAuth.key)) throw authRequired();
    snapshot.key = fingerprint(cookies, site);
    const file = path.join(profile, 'tasks', randomUUID() + '.cookies.txt');
    fs.writeFileSync(file, cookieText(cookies, site), { mode: 0o600 });
    return file;
  }
  async effectiveProxy(url, configured) {
    if (configured) return configured;
    const route = (await this.browserSession.resolveProxy(url)).split(';')[0].trim();
    const match = route.match(/^(PROXY|HTTPS|SOCKS5?|SOCKS4)\s+(.+)$/i);
    if (!match) return '';
    const kind = match[1].toUpperCase();
    const scheme = kind === 'SOCKS4' ? 'socks4' : kind.startsWith('SOCKS') ? 'socks5' : kind === 'HTTPS' ? 'https' : 'http';
    return `${scheme}://${match[2]}`;
  }
  withCookies(spec, onEvent) {
    let task, cancelled = false;
    const controller = new AbortController();
    const handle = { cancel() { cancelled = true; controller.abort(); task?.cancel(); }, promise: null };
    handle.promise = (async () => {
      const site = siteForUrl(spec.url), snapshot = {}, logs = [];
      if (siteForUrl(spec.referrer) === 'behance' && site !== 'behance' && !this.externalTasks.has(spec))
        throw new Error('外部平台托管的视频默认不下载');
      const youtubeVerified = spec.command === 'download' && siteForUrl(spec.url) === 'youtube';
      if (youtubeVerified) await this.ensureYouTube();
      let cookieFile;
      try {
        onEvent?.('log', '正在读取本次任务的登录资料…');
        cookieFile = await waitForPreparation(() => this.cookieSnapshot(site, youtubeVerified, snapshot, controller.signal),
          { signal: controller.signal, label: '读取登录资料' });
        onEvent?.('log', '正在确认网络与系统代理设置…');
        const [proxy, providerUrl] = await Promise.all([
          waitForPreparation(() => this.effectiveProxy(spec.url, spec.settings?.proxy),
            { signal: controller.signal, label: '系统代理解析' }),
          siteForUrl(spec.url) === 'youtube' ? this.youtubeProvider.ensure(onEvent, controller.signal) : undefined,
        ]);
        if (cancelled) { const error = new Error('操作已取消'); error.cancelled = true; throw error; }
        onEvent?.('log', '网络准备完成，正在启动下载器…');
        for (let attempt=0;attempt<2;attempt++) {
          const context=spec.command==='download'&&site==='vimeo'&&this.externalTasks.has(spec) ?
            await this.externalDownloadContext(spec,controller.signal,attempt>0) : spec.context;
          if(cancelled)throw Object.assign(new Error('操作已取消'),{cancelled:true});
          task = this.track(this.runTask({ ...spec, context, ...(siteForUrl(spec.url) === 'youtube' ? { providerUrl } : {}), youtubeVerified, settings: { ...spec.settings, proxy }, cookieFile, userAgent: this.userAgent }, (type, data) => {
            if (type === 'log') { logs.push(safeDetail(data)); if (logs.length > 35) logs.shift(); }
            onEvent?.(type, data);
          }));
          try {return await task.promise;} catch(error) {
            if(attempt || cancelled || spec.command!=='download' || site!=='vimeo' || !this.externalTasks.has(spec) || !spec.context?.vimeo ||
                ![401,403].includes(error.failure?.httpStatus || error.httpStatus) && !/HTTP (?:Error )?(?:401|403)/i.test(error.message+' '+(error.failure?.detail||''))) throw error;
            onEvent?.('log','视频地址暂不可用，正在从原播放器重新获取一次…');
          }
        }
      } catch (error) {
        if (!cancelled && !error.cancelled && spec.command === 'download') {
          const failure = classifyFailure(error, logs, { hadCredentials: !!snapshot.key });
          error.failure = failure;
          if (failure.code === 'AUTH_EXPIRED') await this.rejectCredentials(site, snapshot.key);
        }
        throw error;
      } finally { controller.abort(); if (cookieFile) fs.rmSync(cookieFile, { force: true }); }
    })();
    return this.track(handle);
  }
  async readPage(script, tab = this.tabs.active, waitForValue = false, signal) {
    let timer, stopped = false;
    const sequence = tab.sequence;
    const wrapped = `(() => { if (document.readyState === 'loading') return {waiting: true};
      try { return {value: (${script})}; } catch (error) { return {error: String(error)}; } })()`;
    const read = async () => {
      while (!stopped) {
        if (signal?.aborted || tab.closed || tab.view.webContents.isDestroyed() || tab.sequence !== sequence) throw Object.assign(new Error('页面已切换或解析已取消'), { cancelled: true });
        let result;
        try { result = await tab.view.webContents.mainFrame.executeJavaScript(wrapped); }
        catch { await new Promise(resolve => setTimeout(resolve, 150)); continue; }
        if (result?.error) throw new Error(result.error);
        if (!result?.waiting && (!waitForValue || result?.value != null)) return result?.value;
        await new Promise(resolve => setTimeout(resolve, 150));
      }
    };
    let abort;
    try {
      // WebContents.executeJavaScript waits for all loading to stop, including ads.
      // The document frame can be read as soon as its DOM is ready.
      return await Promise.race([read(),
        new Promise((_, reject) => {
          abort = () => reject(Object.assign(new Error('页面已切换或解析已取消'), { cancelled: true }));
          signal?.addEventListener('abort', abort, { once: true }); if (signal?.aborted) abort();
        }),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('页面响应较慢，请稍后重新解析')), 8000); })]);
    } finally { stopped = true; clearTimeout(timer); signal?.removeEventListener('abort', abort); }
  }
  async inspect(tab = this.tabs.active) {
    const state = tab.state, url = tab.view.webContents.getURL();
    if (!contentKey(url)) throw new Error('请先在左侧打开视频、笔记、作品或商品详情页');
    if (siteForUrl(url) === 'bilibili' && tab.documentKey !== contentKey(url)) throw new Error('播放器正在切换内容，请稍后重新解析');
    this.invalidate(tab); const ticket = ++tab.sequence;
    Object.assign(state, { parsing: true, result: null, inspectionId: null, error: '', logs: [] }); this.publish();
    let context = null;
    try {
      if (siteForUrl(url) === 'xiaohongshu') context = await this.readPage(snapshotScript, tab);
      if (siteForUrl(url) === 'behance') context = await this.readPage(behanceSnapshotScript, tab);
      if (siteForUrl(url) === 'zcool') {
        const deadline = Date.now() + 3500;
        do {
          if (ticket !== tab.sequence || tab.closed || this.closing) return;
          context = await this.readPage(zcoolSnapshotScript, tab, false, tab.assetsAbort.signal);
          if (context && !context.mismatch && (context.images?.length || context.videos?.length)) break;
          await new Promise(resolve => setTimeout(resolve, 150));
        } while (Date.now() < deadline);
      }
      if (siteForUrl(url) === 'taobao') {
        const deadline = Date.now() + 4500;
        do {
          if (ticket !== tab.sequence || tab.closed || this.closing) return;
          context = await this.readPage(taobaoSnapshotScript, tab, false, tab.assetsAbort.signal);
          if (context && !context.mismatch && Object.values(context.groups || {}).some(rows => rows.length)) break;
          await new Promise(resolve => setTimeout(resolve, 200));
        } while (Date.now() < deadline);
      }
      if (siteForUrl(url) === 'bilibili') context = await this.readPage(biliSnapshotScript, tab, /\/video\/BV[0-9A-Za-z]+/.test(new URL(url).pathname));
      if (siteForUrl(url) === 'douyin') {
        context = await resolveDouyinContext({ url, signal: tab.assetsAbort.signal,
          cached: () => this.tabs.rows.map(row => row.douyinPage?.get(url)).find(Boolean),
          read: signal => this.readPage(douyinSnapshotScript, tab, false, signal),
          supplement: signal => this.fetchDouyinContext(url, signal),
          onSupplement: () => this.log('正在补取当前视频详情，无需刷新页面…', tab),
        });
        if (context && ticket === tab.sequence && !tab.closed) tab.douyinPage?.remember(context.detail);
      }
      if (ticket !== tab.sequence || tab.closed || this.closing) return;
      tab.parseTask = this.withCookies({ command: 'inspect', url, context, settings: { ...this.settings } },
        (type, data) => { if (ticket === tab.sequence && type === 'log') this.log(data, tab); });
      const result = await tab.parseTask.promise;
      if (ticket !== tab.sequence || tab.closed || this.closing) return;
      const id = randomUUID(); this.cache.set(id, { result, context, url });
      Object.assign(state, { result, inspectionId: id, error: '' });
    } catch (error) {
      if (ticket === tab.sequence && !error.cancelled) state.error = error.message.replace(/\u001b\[[0-9;]*m/g, '');
    } finally { if (ticket === tab.sequence && !tab.closed) { state.parsing = false; this.publish(); } }
  }
  async inspectExternal(data) {
    const tab=this.tabs.active, record=this.cache.get(data.inspectionId);
    if(!record || data.inspectionId!==tab.state.inspectionId || record.result.site!=='behance' || !this.resultMatchesPage(tab))
      throw new Error('请回到原作品页面后，再手动解析外部视频');
    if(tab.state.parsing)throw new Error('正在解析，请稍候或取消当前解析');
    const context=record.context || await this.readPage(behanceSnapshotScript,tab);
    if(tab.closed || data.inspectionId!==tab.state.inspectionId || !this.resultMatchesPage(tab))throw new Error('页面已切换，请重新解析');
    const entries=externalEntries(context).slice(0,24);
    if(!entries.length)throw new Error('此作品的外部视频暂不支持，目前支持 YouTube 和 Vimeo');
    this.invalidate(tab,{preserveResult:true});const ticket=++tab.sequence, controller=new AbortController(),running=new Set();
    const current=()=>ticket===tab.sequence&&!tab.closed&&!this.closing&&!controller.signal.aborted;
    const handle={cancel(){controller.abort();for(const task of running)task.cancel();},promise:null};
    tab.parseTask=handle;tab.state.parsing=true;tab.state.error='';this.publish();
    handle.promise=(async()=>{
      try {
        this.log('正在读取左侧外部播放器；首次加载可能需要完成网页验证…',tab);
        const configs=await collectVimeo(tab.view.webContents,record.url,entries,{signal:controller.signal,retryUnready:true,
          onProgress:(ready,total,present)=>{if(current())this.log(`已读取 ${ready}/${total} 个 Vimeo 播放器${!present?'；页面尚在加载视频模块':'，正在等待其他视频就绪'}…`,tab);}});
        const outputs=new Array(entries.length), external=new Map();let next=0;
        const work=async()=>{
          while(next<entries.length&&current()){
            const index=next++,entry=entries[index],provider=entry.provider==='youtube'?'YouTube':'Vimeo';
            const mediaContext={...context,externalSource:{key:entry.key},...(configs.has(entry.key)?{vimeo:configs.get(entry.key)}:{})};
            try {
              if(entry.provider==='youtube')await this.ensureYouTube();
              if(!current())return;
              const spec={command:'inspect',url:entry.url,referrer:record.url,context:mediaContext,settings:{...this.settings}};
              this.externalTasks.set(spec,{...entry,tabId:tab.id,sourceUrl:record.url});
              const task=this.withCookies(spec,(type,text)=>{if(current()&&type==='log')this.log(text,tab);});
              running.add(task);let parsed;
              try{parsed=await task.promise;}finally{running.delete(task);}
              if(!current())return;
              const item=parsed.items?.find(row=>row.kind==='video'&&row.formats?.length);
              if(!item || contentKey(item.url)!==contentKey(entry.url))throw new Error('尚未取得可下载画质');
              outputs[index]={...item,externalId:entry.key,provider};
              external.set(entry.key,{entry,context:mediaContext});
            }catch(error){
              if(!current()||error.cancelled)return;
              outputs[index]={kind:'unavailable',url:entry.url,externalId:entry.key,provider,title:`未就绪 · 外部视频 ${index+1}`,author:'',
                message:entry.provider==='youtube'?(error.code==='YOUTUBE_AUTH_REQUIRED'||this.youtubeAuth.status!=='verified'?
                '请先在 YouTube 分组通过 Edge 登录或导入 Cookies，再重新解析外部视频。':'此 YouTube 视频暂未取得画质，请检查网络或访问权限后重新解析外部视频。'):
                '播放器尚未就绪或未返回画质。请在左侧滚动到该视频，完成必要的验证或点击播放后，再重新解析外部视频。'};
              this.log(`${provider} 外部视频暂未就绪：${safeDetail(error.message)}`,tab);
            }
          }
        };
        await Promise.all(Array.from({length:Math.min(3,entries.length)},work));
        if(!current())return;
        record.context=context;record.external=external;
        const base=record.result.items.filter(item=>!item.externalId);
        record.result={...record.result,items:[...base,...outputs.filter(Boolean)],externalParsed:true,externalCount:entries.length,
          skippedExternal:record.result.externalParsed?record.result.skippedExternal:Math.max(0,(record.result.skippedExternal||0)-entries.length)};
        tab.state.result=record.result;
        this.log(`外部视频解析完成：${external.size}/${entries.length} 个已取得画质；下载时请单独选择。`,tab);
      }catch(error){if(current()&&!error.cancelled)tab.state.error=error.message;}
      finally{if(current()){tab.state.parsing=false;this.publish();}}
    })();
    return this.track(handle).promise;
  }
  async externalDownloadContext(spec,signal,force=false) {
    const source=this.externalTasks.get(spec), context=spec.context;
    if(!source || !context?.vimeo)return context;
    if(!force&&Date.now()-context.vimeo.capturedAt<180000)return context;
    const tab=this.tabs.rows.find(row=>row.id===source.tabId&&!row.closed);
    if(!tab?.view || contentKey(tab.view.webContents.getURL())!==contentKey(source.sourceUrl))
      throw new Error('外部视频地址已过期，请回到原作品页面重新解析外部视频');
    const configs=await collectVimeo(tab.view.webContents,source.sourceUrl,[source],{signal,timeout:15000,refresh:true});
    const vimeo=configs.get(source.key);
    if(!vimeo)throw new Error('播放器暂未返回新的媒体地址，请稍后重新解析外部视频');
    spec.context={...context,vimeo};return spec.context;
  }
  async fetchDouyinContext(url, signal) {
    const id = contentKey(url)?.match(/^douyin:(\d+)$/)?.[1];
    if (!id) return null;
    // A plain HTTP request can receive a placeholder page. Use normal browser loading
    // in the same session, without reloading or moving the visible page.
    const probe = new WebContentsView({ webPreferences: { session: this.browserSession,
      contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true } });
    const web = probe.webContents; let stopped = false, timer;
    const close = () => { if (!web.isDestroyed()) web.close({ waitForBeforeUnload: false }); };
    const restrict = (event, details) => {
      try { const next = new URL(typeof details === 'string' ? details : details.url);
        if (next.protocol !== 'https:' || next.hostname !== 'www.douyin.com') event.preventDefault();
      } catch { event.preventDefault(); }
    };
    web.setUserAgent(this.userAgent); web.setAudioMuted(true);
    web.setWindowOpenHandler(() => ({ action: 'deny' }));
    web.on('will-navigate', restrict); web.on('will-redirect', restrict);
    signal.addEventListener('abort', close, { once: true });
    try {
      if (signal.aborted) return null;
      web.loadURL(`https://www.douyin.com/jingxuan?modal_id=${id}`).catch(() => {});
      const read = async () => {
        while (!stopped && !signal.aborted && !web.isDestroyed()) {
          try { const context = await web.mainFrame.executeJavaScript(douyinSnapshotScript);
            if (context?.videoId === id) return context;
          } catch {}
          await new Promise(resolve => setTimeout(resolve, 200));
        }
        return null;
      };
      return await Promise.race([read(), new Promise(resolve => { timer = setTimeout(() => resolve(null), 10000); })]);
    } finally {
      stopped = true; clearTimeout(timer); signal.removeEventListener('abort', close); close();
    }
  }
  async verifyYouTube() {
    const response = await this.browserSession.fetch('https://www.youtube.com/', {
      credentials: 'include', redirect: 'error', signal: AbortSignal.timeout(15000), headers: { 'User-Agent': this.userAgent },
    });
    if (!response.ok) { await response.body?.cancel(); throw new Error('YouTube 登录验证连接失败，请检查网络或代理'); }
    return loginEvidence((await readBody(response, 8 * 1024 * 1024)).toString('utf8'));
  }
  asset(data, mode) {
    const record = this.cache.get(data.inspectionId);
    if (!record || data.inspectionId !== this.state.inspectionId) throw new Error('页面已切换，请重新解析');
    const item = record.result.items[Number(data.itemIndex)], site = record.result.site === 'behance' && !item?.externalId ? 'behance' : siteForUrl(item?.url);
    if (!item || !site) throw new Error('资源不存在');
    const image = item.images?.find(row => row.index === Number(data.imageIndex));
    if (item.kind === 'images' && !image) throw new Error('图片不存在');
    if (mode === 'size') {
      if (!image) throw new Error('当前内容不是图片');
      return this.assets.sourceSize([image.url, ...(image.fallback_urls || [])], site, this.tabs.active.assetsAbort.signal, ['taobao', 'zcool'].includes(site) ? item.url : undefined, site === 'zcool' || (site === 'taobao' && !(image.width && image.height)), !!data.refresh);
    }
    const url = image ? image.preview_url || image.fallback_urls?.at(-1) || image.url : item.thumbnail;
    if (!url) throw new Error('当前页面未提供视频封面');
    return this.assets.preview(url, site, this.tabs.active.assetsAbort.signal, !!data.refresh, ['taobao', 'zcool'].includes(site) ? item.url : undefined);
  }
  async download(data) {
    const tab = this.tabs.active;
    const snapshot = this.cache.get(data.inspectionId);
    if (!snapshot || data.inspectionId !== this.state.inspectionId) throw new Error('页面已切换，请重新解析后下载');
    const item = snapshot.result.items[Number(data.itemIndex)];
    if (!item || !siteForUrl(item.url)) throw new Error('资源不存在，请重新解析');
    const external=snapshot.external?.get(item.externalId);
    if (snapshot.result.site === 'behance' && siteForUrl(item.url) !== 'behance' && (!external || external.entry.url!==item.url)) throw new Error('外部平台托管的视频默认不下载');
    if (!['images', 'video'].includes(item.kind)) throw new Error('请先在视频来源站点打开并解析此视频');
    if (siteForUrl(item.url) === 'youtube') await this.ensureYouTube();
    if (tab.closed || this.tabs.active !== tab || data.inspectionId !== tab.state.inspectionId) throw new Error('页面已切换，请重新解析后下载');
    const indices = Array.isArray(data.indices) ? data.indices.filter(i => item.images?.some(image => image.index === i)) : [];
    if (item.kind === 'images' && !indices.length) throw new Error('请至少选择一张图片');
    const formatId = String(data.formatId || '');
    if (formatId && !item.formats?.some(format => format.id === formatId)) throw new Error('该画质已失效，请重新解析');
    if (this.queue.tasks.filter(t => ['queued', 'running', 'cancelling'].includes(t.status)).length >= 50) throw new Error('队列已满，请等待已有任务完成');
    const spec={ command: 'download', url: item.url, kind: item.kind, context: external?.context || snapshot.context,
      mediaId: item.mediaId, referrer: snapshot.result.site === 'behance' ? snapshot.url : undefined,
      indices, formatId, formatHasAudio: !!item.formats?.find(format => format.id === formatId)?.audio,
      settings: { ...this.settings, audioOnly: !!data.audioOnly, subtitles: !!data.subtitles } };
    if(external)this.externalTasks.set(spec,{...external.entry,tabId:tab.id,sourceUrl:snapshot.url});
    const baseTitle = item.categoryLabel ? `${item.title} · ${item.categoryLabel}` : item.title;
    const title = item.kind === 'images' && indices.length === 1 ? `${baseTitle} · 图片 ${String(indices[0]).padStart(2, '0')}` : baseTitle;
    return this.queue.add(spec,title);
  }
  async importCookies(file, site) {
    const info = fs.statSync(file);
    if (!info.isFile() || info.size > 4 * 1024 * 1024) throw new Error('请选择不超过 4 MB 的 Netscape 格式 Cookies 文本文件');
    const cookies = readCookies(fs.readFileSync(file, 'utf8'), site);
    if (!cookies.length) throw new Error('文件中没有可用的本站点 Cookies，可能已过期或格式不符，请重新导出 Netscape 格式的 Cookies 文件');
    let count = 0;
    for (const cookie of cookies) {
      try { await this.browserSession.cookies.set(cookie); count++; } catch {}
    }
    await this.browserSession.cookies.flushStore(); await this.updateLogin();
    if (!count) throw new Error('没有可导入的有效登录凭据，请重新登录');
    if (site === 'youtube') await this.ensureYouTube(true);
    return count;
  }
  async chooseCookies(site) {
    if (!Object.hasOwn(SITES, site)) throw new Error('请选择支持的站点');
    if (this.state.loginRunning || this.state.importingCookies || this.webLoginRequest) throw new Error('已有登录操作正在进行');
    this.state.importingCookies = true; this.publish();
    try {
      const legacy = path.join(os.homedir(), site === 'youtube' ? '.yt_dlp_gui_cookies.txt' : `.yt_dlp_gui_${site}_cookies.txt`);
      const selection = await dialog.showOpenDialog(this.window, {
        title: `导入 ${SITES[site].name} Cookies`, buttonLabel: '导入',
        properties: ['openFile'], defaultPath: fs.existsSync(legacy) ? legacy : os.homedir(),
        filters: [{ name: 'Netscape Cookies 文本文件', extensions: ['txt'] }, { name: '所有文件', extensions: ['*'] }],
      });
      if (selection.canceled || !selection.filePaths.length || this.closing) return 0;
      return await this.importCookies(selection.filePaths[0], site);
    } finally { this.state.importingCookies = false; this.publish(); }
  }
  async openWebLogin(site) {
    const plan = loginPlan(site);
    if (this.webLoginRequest) {
      if (!this.webLoginRequest.cancelled && this.webLoginRequest.site === site) return this.webLoginRequest.promise;
      throw new Error('登录界面正在打开，请稍候再试');
    }
    if (this.state.loginRunning || this.state.importingCookies) throw new Error('已有登录操作正在进行');
    if (this.tabs.active.group !== site) throw new Error('请先切换到对应站点再登录');
    let tab = this.tabs.active;
    const current = tab.view.webContents.getURL();
    const existing = this.tabs.rows.find(row => row.group === site && !row.closed && loginHost(site, row.url));
    if (existing) tab = this.activateTab(existing.id);
    else if (plan.direct && !loginHost(site, current)) tab = this.openTab(plan.url, { group: site, reuse: true });
    else if (site === 'behance' && !loginHost(site, current) && current !== plan.url) tab = this.openTab(plan.url, { group: site, reuse: true });
    else if (!loginHost(site, current) && siteForUrl(current) !== site) tab = this.openTab(plan.url, { group: site, reuse: true });
    const request = { site, tab, cancelled: false };
    this.webLoginRequest = request; this.state.openingWebLogin = site; this.publish();
    const web = tab.view.webContents;
    // Cancel page-driven navigation away from a modal while it is being opened.
    // Adobe redirects are expected and use the website's own dynamic state.
    const navigating = (_event, details) => {
      const target = typeof details === 'string' ? details : details.url;
      if (!loginHost(site, target)) request.cancelled = true;
    };
    web.on('will-navigate', navigating);
    const read = async () => {
      const script = surfaceScript(site);
      // DOM-ready is sufficient; advertisements may keep the full page loading.
      const pending = (async () => {
        const main = await web.mainFrame.executeJavaScript(script).catch(() => null);
        if (main?.ready) return [main];
        const frames = web.mainFrame.framesInSubtree.filter(frame => frame !== web.mainFrame &&
          loginHost(site, frame.url) && main?.frames?.includes(new URL(frame.url).hostname)).slice(0, 6);
        return [main, ...await Promise.all(frames.map(frame => frame.executeJavaScript(script).catch(() => null)))];
      })();
      let timer;
      try {
        const rows = await Promise.race([pending, new Promise(resolve => { timer = setTimeout(() => resolve([]), 900); })]);
        return rows.find(row => row?.ready) || rows.find(row => row?.canTrigger) || {};
      } finally { clearTimeout(timer); }
    };
    request.promise = (async () => {
      try {
        await waitForLogin({ probe: read, timeoutMs: site === 'douyin' ? 45000 : 30000,
          trigger: async () => {
            this.window.focus(); web.focus();
            // Native focus changes are asynchronous on Windows. Sending input
            // in the same tick can drop the first click in a newly opened view.
            await new Promise(resolve => setTimeout(resolve, 150));
            if (request.cancelled || tab.closed || this.closing || this.tabs.activeId !== tab.id || web.isDestroyed()) return;
            const result = await web.mainFrame.executeJavaScript(surfaceScript(site, true)).catch(() => null);
            if (!result?.target || request.cancelled || tab.closed || this.closing || this.tabs.activeId !== tab.id) return;
            const { x, y } = result.target;
            if (!Number.isInteger(x) || !Number.isInteger(y) || x < 0 || y < 0 || x >= this.browserBounds.width || y >= this.browserBounds.height) return;
            web.sendInputEvent({ type: 'mouseMove', x, y });
            web.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount: 1 });
            web.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount: 1 });
          },
          active: () => !request.cancelled && !tab.closed && !this.closing && this.tabs.activeId === tab.id && !web.isDestroyed(),
        });
      } finally {
        if (!web.isDestroyed()) web.removeListener('will-navigate', navigating);
        if (this.webLoginRequest === request) { this.webLoginRequest = null; this.state.openingWebLogin = null; this.publish(); }
      }
    })();
    return request.promise;
  }
  async login(site) {
    if (!SITES[site]) throw new Error('请先选择支持的站点');
    if (this.loginTask || this.state.loginRunning || this.state.importingCookies || this.webLoginRequest) throw new Error('已有登录操作正在进行');
    const cookieFile = path.join(profile, 'tasks', randomUUID() + '.cookies.txt');
    const tab = this.tabs.active;
    this.state.loginRunning = site; this.publish();
    try {
      const proxy = await this.effectiveProxy(SITES[site].home, this.settings.proxy);
      this.loginTask = this.track(pythonTask({ command: 'login', site, cookieFile, proxy },
        (type, data) => { if (type === 'log' && !tab.closed) this.log(data, tab); }));
      await this.loginTask.promise; await this.importCookies(cookieFile, site);
      if (!tab.closed) this.log('登录凭据已同步，可重新解析当前内容', tab);
    }
    finally {
      this.loginTask = null; this.state.loginRunning = null;
      if (fs.existsSync(cookieFile)) fs.unlinkSync(cookieFile); this.publish();
    }
  }
  async invoke(method, data = {}) {
    if (this.closing && method !== 'state') throw new Error('采集工具正在关闭，请稍后重新启动');
    if (method === 'state') return this.publicState();
    if (method === 'layout') return this.layout(data);
    if (method === 'resize-start') { this.paneResizing = true; return; }
    if (method === 'resize-end') { this.endPaneResize(); return; }
    if (method === 'panel-width') {
      if (!Number.isFinite(data.width)) throw new Error('面板宽度无效');
      this.settings.panelWidth = Math.round(Math.min(Math.max(330, this.window.getContentSize()[0] - 444), Math.max(330, data.width)));
      this.publish(); return this.settings.panelWidth;
    }
    if (method === 'navigate') return this.navigate(data.url);
    if (method === 'switch-site') { this.switchSite(data.site); return; }
    if (method === 'new-tab') { this.openTab(SITES[this.tabs.active.group].home); return; }
    if (method === 'activate-tab') { this.activateTab(String(data.id)); return; }
    if (method === 'close-tab') { this.closeTab(String(data.id)); return; }
    if (method === 'back') { if (this.browser.webContents.navigationHistory.canGoBack()) this.browser.webContents.navigationHistory.goBack(); return; }
    if (method === 'forward') { if (this.browser.webContents.navigationHistory.canGoForward()) this.browser.webContents.navigationHistory.goForward(); return; }
    if (method === 'reload') { if (this.webLoginRequest) this.webLoginRequest.cancelled = true; this.browser.webContents.reload(); return; }
    if (method === 'inspect') return this.inspect();
    if (method === 'inspect-external') return this.inspectExternal(data);
    if (method === 'cancel-inspect') { this.invalidate(this.tabs.active,{preserveResult:true}); this.publish(); return; }
    if (method === 'download') return this.download(data);
    if (method === 'preview' || method === 'image-size') return this.asset(data, method === 'preview' ? 'preview' : 'size');
    if (method === 'cancel-task') return this.queue.cancel(String(data.id));
    if (method === 'clear-history') {
      const removed = this.queue.clearFinished();
      this.persist();
      return removed;
    }
    if (method === 'retry-task') {
      const task = this.queue.tasks.find(row => row.id === String(data.id));
      if (siteForUrl(task?.spec?.url) === 'youtube') await this.ensureYouTube();
      return this.queue.retry(String(data.id));
    }
    if (method === 'login') return this.login(data.site);
    if (method === 'web-login') return this.openWebLogin(data.site);
    if (method === 'cancel-login') { this.loginTask?.cancel(); return; }
    if (method === 'import-cookies') return this.chooseCookies(data.site);
    if (method === 'choose-folder') {
      const result = await dialog.showOpenDialog(this.window, { properties: ['openDirectory', 'createDirectory'], defaultPath: this.settings.saveDir });
      if (!result.canceled) { this.settings.saveDir = result.filePaths[0]; this.persist(); this.publish(); }
      return;
    }
    if (method === 'open-folder') {
      const task = data.id ? this.queue.tasks.find(t => t.id === String(data.id)) : null;
      if (data.id && !task) throw new Error('该任务记录已清除。');
      return openFolder(data.id ? task.result?.folder : this.settings.saveDir, folder => shell.openPath(folder), !!data.id);
    }
    if (method === 'settings') {
      const proxy = String(data.proxy || '').trim();
      if (proxy) {
        const parsed = new URL(proxy);
        if (!['http:', 'https:', 'socks5:', 'socks5h:'].includes(parsed.protocol) || parsed.username || parsed.password) throw new Error('请使用不含账号密码的 HTTP 或 SOCKS5 代理地址');
      }
      if (!['original', 'jpg', 'png', 'webp'].includes(data.imageFormat)) throw new Error('图片格式无效');
      const limits = concurrencySettings({
        globalConcurrency: data.globalConcurrency ?? this.settings.globalConcurrency,
        siteConcurrency: data.siteConcurrency ?? this.settings.siteConcurrency,
      }, true);
      const wasAutomatic = this.settings.autoParse;
      const previousSettings = this.settings;
      this.settings = { ...this.settings, proxy, imageFormat: data.imageFormat, autoParse: !!data.autoParse, ...limits };
      try { await this.applyProxy(); } catch (error) { this.settings = previousSettings; throw error; }
      this.queue.setLimits(limits); this.persist(); this.publish();
      if (!this.settings.autoParse) this.tabs.rows.forEach(tab => clearTimeout(tab.parseTimer));
      else if (!wasAutomatic) this.pageReady(this.tabs.active);
      return;
    }
    throw new Error('不支持此操作');
  }
  close() {
    if (this.closing) return;
    this.closing = true; this.tabs.rows.forEach(tab => this.invalidate(tab)); this.queue.close(); this.youtubeProvider.close(); this.handles.forEach(task => task.cancel());
    this.state.error = '正在停止后台任务并保存记录…'; this.publish();
    const finish = () => {
      if (this.handles.size) { setTimeout(finish, 150); return; }
      clearTimeout(this.saveTimer); this.persist();
      this.destroyViews(); this.allowClose = true; this.window.close();
    }; finish();
  }
}

let collector;
app.whenReady().then(async () => {
  collector = new CollectorApp();
  ipcMain.handle('collector:invoke', async (event, method, data) => {
    if (event.sender !== collector.window.webContents || event.senderFrame !== event.sender.mainFrame ||
        event.senderFrame.url !== pathToFileURL(path.join(__dirname, 'ui', 'index.html')).href) throw new Error('请求来源不受信任');
    try { return { ok: true, data: await collector.invoke(method, data) }; }
    catch (error) { return { ok: false, error: error.message, code: error.code }; }
  });
  await collector.create();
  if (smoke) await require('./smoke.cjs').run(collector, profile, snapshotScript);
  if (liveCheck) await require('./live-check.cjs').run(collector, profile);
  if (persistenceCheck) {
    await collector.healthTask.promise;
    const assert = require('node:assert/strict');
    assert.ok(collector.state.login.zcool, 'ZCOOL login should recover from its persisted credential pair');
    assert.ok(!collector.state.loginExpired.zcool, 'a rejected prior ZCOOL session must not invalidate replacement credentials');
    console.log('Restart persistence: ZCOOL login credential pair restored');
    assert.equal(collector.state.login.xiaohongshu, true);
    assert.equal(collector.settings.autoParse, false);
    assert.equal(collector.settings.panelWidth, 450);
    assert.equal(collector.settings.globalConcurrency, 4);
    assert.equal(collector.settings.siteConcurrency, 2);
    assert.deepEqual(collector.queue.limits, { globalConcurrency: 4, siteConcurrency: 2 });
    assert.equal(collector.queue.tasks[0].status, 'completed');
    assert.equal(collector.queue.tasks.find(task => task.title === '受控失败：网络超时').failure.code, 'NETWORK_TIMEOUT');
    assert.equal(collector.state.loginExpired.bilibili, true);
    console.log('Restart persistence: failure reasons and rejected credential state OK');
    console.log('Restart persistence: browser login, settings and task history OK');
    assert.ok(collector.tabs.rows.length >= 3);
    assert.equal(collector.tabs.active.group, 'xiaohongshu');
    assert.ok(collector.tabs.rows.some(tab => tab.group === 'vimeo'));
    assert.ok(collector.tabs.rows.some(tab => tab.group === 'douyin'));
    assert.equal(collector.tabs.rows.filter(tab => tab.view).length, 1);
    console.log('Restart persistence: site tabs restored lazily');
    collector.destroyViews(); collector.window.destroy(); app.exit(0);
  }
}).catch(error => { console.error(error); app.exit(1); });
app.on('window-all-closed', () => app.quit());
module.exports = { CollectorApp, snapshotScript };
