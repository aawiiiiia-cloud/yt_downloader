'use strict';
const { app, BrowserWindow, WebContentsView, ipcMain, dialog, shell, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { randomUUID } = require('node:crypto');
const { pathToFileURL } = require('node:url');
const { SITES, navigationUrl, siteForUrl, contentKey, cookieText, readCookies, hasAuth, DownloadQueue } = require('./model.cjs');
const { pythonTask } = require('./bridge.cjs');
const { Assets, readBody } = require('./assets.cjs');
const { YouTubeAuth, loginEvidence, fingerprint, authRequired } = require('./youtube-auth.cjs');
const { SiteTabs, linkScript } = require('./site-tabs.cjs');
const { DouyinPage, snapshotScript: douyinSnapshotScript } = require('./douyin-page.cjs');

const smoke = process.argv.includes('--collector-smoke');
const liveCheck = process.argv.includes('--collector-live-check');
const persistenceCheck = process.argv.includes('--collector-persistence-check');
const profile = smoke || liveCheck ? path.join(__dirname, '..', 'electron-artifacts', `${smoke ? 'smoke' : 'live'}-${Date.now()}`) :
  process.env.YT_COLLECTOR_HOME || path.join(os.homedir(), '.media-collector');
app.setName('拾集 · 媒体采集');
app.setPath('userData', profile);
if (liveCheck) {
  require('electron').crashReporter.start({ productName: 'MediaCollectorTest', uploadToServer: false });
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
    this.settings = { saveDir: legacy.save_dir || path.join(os.homedir(), 'Downloads'), proxy: legacy.proxy || '',
      autoParse: true, imageFormat: 'original', imageQuality: 95, panelWidth: 390, ...readJson(path.join(profile, 'settings.json'), {}) };
    this.shared = { health: null, login: {}, loginRunning: null, importingCookies: false };
    this.tabs = new SiteTabs(readJson(path.join(profile, 'tabs.json'), null));
    this.tabs.rows.forEach(tab => this.initializeTab(tab));
    this.handles = new Set(); this.cache = new Map(); this.closing = false;
    this.youtubeAuth = new YouTubeAuth(() => this.browserSession.cookies.get({}), () => this.verifyYouTube(), () => this.publish());
    this.queue = new DownloadQueue((spec, onEvent) => this.withCookies(spec, onEvent), () => this.publish());
    const history = readJson(path.join(profile, 'history.json'), []);
    this.queue.tasks = (Array.isArray(history) ? history : []).filter(t => t && typeof t === 'object').slice(0, 60).map(t => ({
      ...t, status: ['queued', 'running', 'cancelling'].includes(t.status) ? 'failed' : t.status,
      error: ['queued', 'running', 'cancelling'].includes(t.status) ? '上次任务已中断，请重新解析后下载' : t.error,
    }));
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
  publicState() { return { ...this.state, activeTabId: this.tabs.activeId, activeGroup: this.tabs.active.group,
    tabs: this.tabs.rows.map(tab => ({ id: tab.id, group: tab.group, title: tab.title,
      url: tab.url, loading: tab.state?.loading || false, parsing: tab.state?.parsing || false })),
    settings: this.settings, youtubeAuth: this.youtubeAuth.status,
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
    writeJson(path.join(profile, 'tabs.json'), this.tabs.serialize());
  }
  track(handle) {
    this.handles.add(handle);
    handle.promise.then(() => this.handles.delete(handle), () => this.handles.delete(handle));
    return handle;
  }
  log(text, tab = this.tabs.active) { tab.state.logs = [...tab.state.logs, String(text)].slice(-35); this.publish(); }
  async create() {
    this.browserSession = session.fromPartition('persist:collector-web');
    this.assets = new Assets((url, options) => this.browserSession.fetch(url, { ...options,
      headers: { 'User-Agent': this.userAgent, ...options.headers } }));
    this.browserSession.setPermissionRequestHandler((_contents, permission, callback) => callback(permission === 'fullscreen'));
    this.browserSession.setPermissionCheckHandler((_contents, permission) => permission === 'fullscreen');
    await this.applyProxy();
    this.window = new BrowserWindow({ width: 1440, height: 920, minWidth: 1060, minHeight: 700,
      show: !smoke && !liveCheck && !persistenceCheck, backgroundColor: '#f5f6f8', title: '拾集 · 媒体采集', autoHideMenuBar: true,
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
    this.healthTask = this.track(pythonTask({ command: 'health' }));
    this.healthTask.promise.then(health => { this.state.health = health; this.publish(); }, error => { this.state.error = error.message; this.publish(); });
    return this;
  }
  ensureView(tab) {
    this.initializeTab(tab);
    if (tab.view) return;
    const view = tab.view = new WebContentsView({ webPreferences: { session: this.browserSession,
      contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true } });
    this.window.contentView.addChildView(view); view.setVisible(false); view.setBounds(this.browserBounds);
    const web = view.webContents;
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
    web.setWindowOpenHandler(({ url }) => {
      try { const next = navigationUrl(url); setImmediate(() => {
        if (this.closing || tab.closed) return;
        try { this.openTab(next, { group: siteForUrl(next) || tab.group, reuse: true }); }
        catch (error) { this.state.error = error.message; this.publish(); }
      }); } catch {}
      return { action: 'deny' };
    });
    web.on('will-navigate', (event, details) => {
      try {
        const next = navigationUrl(typeof details === 'string' ? details : details.url), group = siteForUrl(next);
        const key = contentKey(next), current = contentKey(tab.state.url);
        const sameBiliVideo = current?.startsWith('bilibili:') && current.split(':')[1] === key?.split(':')[1];
        if ((group && group !== tab.group) || (key && key !== current && !sameBiliVideo)) {
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
      if (inPlace) this.invalidate(tab);
      tab.state.loading = true; this.publish();
    });
    // A cancelled/provisional navigation must not discard the source tab's resources.
    web.on('did-navigate', (_event, url) => { this.invalidate(tab); tab.douyinPage?.reset(); tab.documentKey = contentKey(url); });
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
    const load = tab.url ? web.loadURL(tab.url) : web.loadFile(path.join(__dirname, 'ui', 'welcome.html'));
    load.catch(() => {});
  }
  pauseTab(tab) {
    if (!tab.view || tab.view.webContents.isDestroyed()) return;
    tab.view.webContents.setAudioMuted(true);
    tab.view.webContents.mainFrame.executeJavaScript("document.querySelectorAll('video,audio').forEach(media => media.pause())").catch(() => {});
  }
  activateTab(id) {
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
    tab.closed = true; this.invalidate(tab); tab.assetsAbort.abort(); tab.douyinPage?.dispose();
    if (tab.view && !tab.view.webContents.isDestroyed()) {
      this.window.contentView.removeChildView(tab.view); tab.view.webContents.close({ waitForBeforeUnload: false });
    }
  }
  destroyViews() { this.tabs.rows.forEach(tab => this.disposeTab(tab)); }
  invalidate(tab = this.tabs.active) {
    tab.sequence++; clearTimeout(tab.parseTimer); tab.parseTask?.cancel();
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
    if (this.settings.autoParse && contentKey(state.url) && !state.result && !state.parsing)
      tab.parseTimer = setTimeout(() => this.inspect(tab).catch(error => { state.error = error.message; this.publish(); }), 850);
  }
  navigate(url) {
    const target = navigationUrl(url), group = siteForUrl(target);
    if (group && group !== this.tabs.active.group) { this.openTab(target, { group, reuse: true }); return; }
    this.invalidate(); this.tabs.active.url = this.state.url = target;
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
    for (const site of Object.keys(SITES)) this.state.login[site] = hasAuth(cookies, site);
    this.youtubeAuth.observe(cookies);
    this.publish();
  }
  async cookieSnapshot(site, verified = false) {
    const cookies = await this.browserSession.cookies.get({});
    if (verified && (this.youtubeAuth.status !== 'verified' || fingerprint(cookies) !== this.youtubeAuth.key)) throw authRequired();
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
    const handle = { cancel() { cancelled = true; task?.cancel(); }, promise: null };
    handle.promise = (async () => {
      const youtubeVerified = spec.command === 'download' && siteForUrl(spec.url) === 'youtube';
      if (youtubeVerified) await this.youtubeAuth.ensure();
      const cookieFile = await this.cookieSnapshot(siteForUrl(spec.url), youtubeVerified);
      try {
        const proxy = await this.effectiveProxy(spec.url, spec.settings?.proxy);
        if (cancelled) { const error = new Error('操作已取消'); error.cancelled = true; throw error; }
        task = this.track(pythonTask({ ...spec, youtubeVerified, settings: { ...spec.settings, proxy }, cookieFile, userAgent: this.userAgent }, (type, data) => {
          if (type === 'auth-invalid') { this.youtubeAuth.status = 'expired'; this.youtubeAuth.until = 0; this.publish(); }
          onEvent?.(type, data);
        }));
        return await task.promise;
      } finally { fs.unlinkSync(cookieFile); }
    })();
    return this.track(handle);
  }
  async readPage(script, tab = this.tabs.active, waitForValue = false) {
    let timer, stopped = false;
    const sequence = tab.sequence;
    const wrapped = `(() => { if (document.readyState === 'loading') return {waiting: true};
      try { return {value: (${script})}; } catch (error) { return {error: String(error)}; } })()`;
    const read = async () => {
      while (!stopped) {
        if (tab.closed || tab.view.webContents.isDestroyed() || tab.sequence !== sequence) throw Object.assign(new Error('页面已切换或解析已取消'), { cancelled: true });
        let result;
        try { result = await tab.view.webContents.mainFrame.executeJavaScript(wrapped); }
        catch { await new Promise(resolve => setTimeout(resolve, 150)); continue; }
        if (result?.error) throw new Error(result.error);
        if (!result?.waiting && (!waitForValue || result?.value != null)) return result?.value;
        await new Promise(resolve => setTimeout(resolve, 150));
      }
    };
    try {
      // WebContents.executeJavaScript waits for all loading to stop, including ads.
      // The document frame can be read as soon as its DOM is ready.
      return await Promise.race([read(),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('页面响应较慢，请稍后重新解析')), 8000); })]);
    } finally { stopped = true; clearTimeout(timer); }
  }
  async inspect(tab = this.tabs.active) {
    const state = tab.state, url = tab.view.webContents.getURL();
    if (!contentKey(url)) throw new Error('请先在左侧打开支持的视频或小红书笔记详情页（YouTube、B站、小红书、Vimeo、抖音）');
    if (siteForUrl(url) === 'bilibili' && tab.documentKey !== contentKey(url)) throw new Error('播放器正在切换内容，请稍后重新解析');
    this.invalidate(tab); const ticket = ++tab.sequence;
    Object.assign(state, { parsing: true, result: null, inspectionId: null, error: '', logs: [] }); this.publish();
    let context = null;
    try {
      if (siteForUrl(url) === 'xiaohongshu') context = await this.readPage(snapshotScript, tab);
      if (siteForUrl(url) === 'bilibili') context = await this.readPage(biliSnapshotScript, tab, /\/video\/BV[0-9A-Za-z]+/.test(new URL(url).pathname));
      if (siteForUrl(url) === 'douyin') {
        // Feed/modal data may arrive after DOM ready; allow the normal player time to load.
        const deadline = Date.now() + 7000;
        do {
          context = tab.douyinPage?.get(url) || await this.readPage(douyinSnapshotScript, tab);
          if (context || ticket !== tab.sequence || tab.closed || this.closing) break;
          await new Promise(resolve => setTimeout(resolve, 200));
        } while (Date.now() < deadline);
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
    const item = record.result.items[Number(data.itemIndex)], site = siteForUrl(item?.url);
    if (!item || !site) throw new Error('资源不存在');
    const image = item.images?.find(row => row.index === Number(data.imageIndex));
    if (item.kind === 'images' && !image) throw new Error('图片不存在');
    if (mode === 'size') {
      if (!image) throw new Error('当前内容不是图片');
      return this.assets.sourceSize([image.url, ...(image.fallback_urls || [])], site, this.tabs.active.assetsAbort.signal);
    }
    const url = image ? image.fallback_urls?.at(-1) || image.url : item.thumbnail;
    if (!url) throw new Error('当前页面未提供视频封面');
    return this.assets.preview(url, site, this.tabs.active.assetsAbort.signal, !!data.refresh);
  }
  async download(data) {
    const tab = this.tabs.active;
    const snapshot = this.cache.get(data.inspectionId);
    if (!snapshot || data.inspectionId !== this.state.inspectionId) throw new Error('页面已切换，请重新解析后下载');
    const item = snapshot.result.items[Number(data.itemIndex)];
    if (!item || !siteForUrl(item.url)) throw new Error('资源不存在，请重新解析');
    if (siteForUrl(item.url) === 'youtube') await this.youtubeAuth.ensure();
    if (tab.closed || this.tabs.active !== tab || data.inspectionId !== tab.state.inspectionId) throw new Error('页面已切换，请重新解析后下载');
    const indices = Array.isArray(data.indices) ? data.indices.filter(i => item.images?.some(image => image.index === i)) : [];
    if (item.kind === 'images' && !indices.length) throw new Error('请至少选择一张图片');
    const formatId = String(data.formatId || '');
    if (formatId && !item.formats?.some(format => format.id === formatId)) throw new Error('该画质已失效，请重新解析');
    if (this.queue.tasks.filter(t => ['queued', 'running', 'cancelling'].includes(t.status)).length >= 50) throw new Error('队列已满，请等待已有任务完成');
    return this.queue.add({ command: 'download', url: item.url, kind: item.kind, context: snapshot.context,
      indices, formatId, formatHasAudio: !!item.formats?.find(format => format.id === formatId)?.audio,
      settings: { ...this.settings, audioOnly: !!data.audioOnly, subtitles: !!data.subtitles } }, item.title);
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
    if (site === 'youtube') await this.youtubeAuth.ensure(true);
    return count;
  }
  async chooseCookies(site) {
    if (!Object.hasOwn(SITES, site)) throw new Error('请选择支持的站点');
    if (this.state.loginRunning || this.state.importingCookies) throw new Error('已有登录操作正在进行');
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
  async login(site) {
    if (!SITES[site]) throw new Error('请先选择支持的站点');
    if (this.loginTask || this.state.loginRunning || this.state.importingCookies) throw new Error('已有登录操作正在进行');
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
    if (method === 'reload') { this.browser.webContents.reload(); return; }
    if (method === 'inspect') return this.inspect();
    if (method === 'cancel-inspect') { this.invalidate(); this.publish(); return; }
    if (method === 'download') return this.download(data);
    if (method === 'preview' || method === 'image-size') return this.asset(data, method === 'preview' ? 'preview' : 'size');
    if (method === 'cancel-task') return this.queue.cancel(String(data.id));
    if (method === 'retry-task') {
      const task = this.queue.tasks.find(row => row.id === String(data.id));
      if (siteForUrl(task?.spec?.url) === 'youtube') await this.youtubeAuth.ensure();
      return this.queue.retry(String(data.id));
    }
    if (method === 'login') return this.login(data.site);
    if (method === 'cancel-login') { this.loginTask?.cancel(); return; }
    if (method === 'import-cookies') return this.chooseCookies(data.site);
    if (method === 'choose-folder') {
      const result = await dialog.showOpenDialog(this.window, { properties: ['openDirectory', 'createDirectory'], defaultPath: this.settings.saveDir });
      if (!result.canceled) { this.settings.saveDir = result.filePaths[0]; this.persist(); this.publish(); }
      return;
    }
    if (method === 'open-folder') {
      let folder = this.settings.saveDir;
      if (data.id) folder = this.queue.tasks.find(t => t.id === data.id)?.result?.folder || folder;
      if (!fs.existsSync(folder) || !fs.statSync(folder).isDirectory()) throw new Error('保存目录尚不存在');
      return shell.openPath(folder);
    }
    if (method === 'settings') {
      const proxy = String(data.proxy || '').trim();
      if (proxy) {
        const parsed = new URL(proxy);
        if (!['http:', 'https:', 'socks5:', 'socks5h:'].includes(parsed.protocol) || parsed.username || parsed.password) throw new Error('请使用不含账号密码的 HTTP 或 SOCKS5 代理地址');
      }
      if (!['original', 'jpg', 'png', 'webp'].includes(data.imageFormat)) throw new Error('图片格式无效');
      this.settings = { ...this.settings, proxy, imageFormat: data.imageFormat, autoParse: !!data.autoParse };
      await this.applyProxy(); this.persist(); this.publish(); return;
    }
    throw new Error('不支持此操作');
  }
  close() {
    if (this.closing) return;
    this.closing = true; this.tabs.rows.forEach(tab => this.invalidate(tab)); this.queue.close(); this.handles.forEach(task => task.cancel());
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
    assert.equal(collector.state.login.xiaohongshu, true);
    assert.equal(collector.settings.autoParse, false);
    assert.equal(collector.settings.panelWidth, 450);
    assert.equal(collector.queue.tasks[0].status, 'completed');
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
