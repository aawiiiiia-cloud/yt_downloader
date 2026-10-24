'use strict';
const { app, BrowserWindow, WebContentsView, ipcMain, dialog, shell, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { randomUUID } = require('node:crypto');
const { pathToFileURL } = require('node:url');
const { SITES, navigationUrl, siteForUrl, contentKey, cookieText, readCookies, hasAuth, DownloadQueue } = require('./model.cjs');
const { pythonTask } = require('./bridge.cjs');

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
      pages: (video.pages || []).slice(0, 200).map(p => ({page: p.page, part: p.part}))}, playInfo: play }));
})()`;

class CollectorApp {
  constructor() {
    fs.mkdirSync(path.join(profile, 'tasks'), { recursive: true });
    const legacy = smoke || liveCheck || persistenceCheck ? {} : readJson(path.join(os.homedir(), '.yt_dlp_gui.json'), {});
    this.settings = { saveDir: legacy.save_dir || path.join(os.homedir(), 'Downloads'), proxy: legacy.proxy || '',
      autoParse: true, imageFormat: 'original', imageQuality: 95, ...readJson(path.join(profile, 'settings.json'), {}) };
    this.state = { url: '', title: '', site: null, loading: false, parsing: false, result: null,
      inspectionId: null, error: '', health: null, login: {}, loginRunning: null, logs: [] };
    this.handles = new Set(); this.sequence = 0; this.cache = new Map(); this.closing = false;
    this.queue = new DownloadQueue((spec, onEvent) => this.withCookies(spec, onEvent), () => this.publish());
    const history = readJson(path.join(profile, 'history.json'), []);
    this.queue.tasks = (Array.isArray(history) ? history : []).filter(t => t && typeof t === 'object').slice(0, 60).map(t => ({
      ...t, status: ['queued', 'running', 'cancelling'].includes(t.status) ? 'failed' : t.status,
      error: ['queued', 'running', 'cancelling'].includes(t.status) ? '上次任务已中断，请重新解析后下载' : t.error,
    }));
  }
  publicState() { return { ...this.state, settings: this.settings,
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
  }
  track(handle) {
    this.handles.add(handle);
    handle.promise.then(() => this.handles.delete(handle), () => this.handles.delete(handle));
    return handle;
  }
  log(text) { this.state.logs = [...this.state.logs, String(text)].slice(-35); this.publish(); }
  async create() {
    this.browserSession = session.fromPartition('persist:collector-web');
    this.browserSession.setPermissionRequestHandler((_contents, permission, callback) => callback(permission === 'fullscreen'));
    this.browserSession.setPermissionCheckHandler((_contents, permission) => permission === 'fullscreen');
    await this.applyProxy();
    this.window = new BrowserWindow({ width: 1440, height: 920, minWidth: 1060, minHeight: 700,
      show: !smoke && !liveCheck && !persistenceCheck, backgroundColor: '#f5f6f8', title: '拾集 · 媒体采集', autoHideMenuBar: true,
      webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true },
    });
    this.browser = new WebContentsView({ webPreferences: { session: this.browserSession,
      contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true } });
    this.window.contentView.addChildView(this.browser);
    this.browser.setBounds({ x: 16, y: 114, width: 940, height: 740 });
    // Use the bundled Chromium UA for both browsing and extraction, without the app product token.
    const platform = process.platform === 'win32' ? 'Windows NT 10.0; Win64; x64' : process.platform === 'darwin' ? 'Macintosh; Intel Mac OS X 10_15_7' : 'X11; Linux x86_64';
    this.userAgent = `Mozilla/5.0 (${platform}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${process.versions.chrome} Safari/537.36`;
    this.browser.webContents.setUserAgent(this.userAgent);
    this.browserSession.setUserAgent(this.userAgent);
    session.defaultSession.setUserAgent(this.userAgent);
    this.window.webContents.setUserAgent(this.userAgent);
    this.browser.webContents.setWindowOpenHandler(({ url }) => {
      try { const next = navigationUrl(url); setImmediate(() => this.navigate(next)); } catch {}
      return { action: 'deny' };
    });
    this.browser.webContents.on('will-navigate', (event, details) => {
      try { navigationUrl(typeof details === 'string' ? details : details.url); } catch { event.preventDefault(); }
    });
    this.browser.webContents.on('did-start-navigation', (_e, url, inPlace, isMainFrame) => {
      if (!isMainFrame) return;
      // Analytics often replace query parameters without changing the note/video.
      // Only invalidate an in-page result when its actual content identity changes.
      if (inPlace && contentKey(url) && contentKey(url) === contentKey(this.state.url)) return;
      this.invalidate(); this.state.loading = true; this.publish();
    });
    this.browser.webContents.on('did-navigate', (_event, url) => { this.documentKey = contentKey(url); });
    this.browser.webContents.on('did-navigate-in-page', (_e, url, isMainFrame) => {
      if (!isMainFrame) return;
      // B站的静态 __playinfo__ 可能在 SPA 分P切换后仍指向上一段。
      // 第一阶段以新内容链接重新加载，确保浏览器快照对应当前视频/分P。
      if (siteForUrl(url) === 'bilibili' && contentKey(url) && contentKey(url) !== this.documentKey) {
        this.browser.webContents.reload(); return;
      }
      this.pageReady();
    });
    this.browser.webContents.on('did-finish-load', () => this.pageReady());
    this.browser.webContents.on('dom-ready', () => this.pageReady());
    this.browser.webContents.on('did-stop-loading', () => { this.state.loading = false; this.publish(); });
    this.browser.webContents.on('page-title-updated', () => { this.state.title = this.browser.webContents.getTitle(); this.publish(); });
    this.browser.webContents.on('did-fail-load', (_event, code, description, _url, main) => {
      if (main && code !== -3) { this.state.loading = false; this.state.error = '网页加载失败，请检查网络或代理设置（' + description + '）'; this.publish(); }
    });
    this.browserSession.cookies.on('changed', () => this.updateLogin().catch(() => {}));
    this.window.on('close', event => {
      if (this.allowClose) return;
      event.preventDefault(); this.close();
    });
    await this.window.loadFile(path.join(__dirname, 'ui', 'index.html'));
    await this.browser.webContents.loadFile(path.join(__dirname, 'ui', 'welcome.html'));
    await this.updateLogin();
    this.healthTask = this.track(pythonTask({ command: 'health' }));
    this.healthTask.promise.then(health => { this.state.health = health; this.publish(); }, error => { this.state.error = error.message; this.publish(); });
    return this;
  }
  invalidate() {
    this.sequence++; clearTimeout(this.parseTimer); this.parseTask?.cancel();
    Object.assign(this.state, { result: null, inspectionId: null, parsing: false, error: '', logs: [] });
  }
  pageReady() {
    this.state.url = this.browser.webContents.getURL(); this.state.site = siteForUrl(this.state.url);
    this.state.title = this.browser.webContents.getTitle(); this.state.loading = false;
    this.publish(); clearTimeout(this.parseTimer);
    if (this.settings.autoParse && contentKey(this.state.url) && !this.state.result && !this.state.parsing)
      this.parseTimer = setTimeout(() => this.inspect().catch(error => { this.state.error = error.message; this.publish(); }), 850);
  }
  navigate(url) {
    const target = navigationUrl(url); this.invalidate();
    this.state.url = target; this.state.site = siteForUrl(target); this.state.loading = true; this.publish();
    this.browser.webContents.loadURL(target).catch(() => {});
  }
  layout(bounds) {
    const [width, height] = this.window.getContentSize();
    const rect = Object.fromEntries(['x', 'y', 'width', 'height'].map(key => [key, Math.round(Number(bounds[key]) || 0)]));
    if (rect.x < 0 || rect.y < 0 || rect.width < 200 || rect.height < 100 || rect.x + rect.width > width + 2 || rect.y + rect.height > height + 2) return;
    this.browser.setBounds(rect);
  }
  async applyProxy() {
    const proxy = this.settings.proxy;
    const config = proxy ? { mode: 'fixed_servers', proxyRules: proxy.replace(/^socks5h:/i, 'socks5:'), proxyBypassRules: '<local>' } : { mode: 'system' };
    await Promise.all([this.browserSession.setProxy(config), session.defaultSession.setProxy(config)]);
  }
  async updateLogin() {
    const cookies = await this.browserSession.cookies.get({});
    for (const site of Object.keys(SITES)) this.state.login[site] = hasAuth(cookies, site);
    this.publish();
  }
  async cookieSnapshot(site) {
    const cookies = await this.browserSession.cookies.get({});
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
      const cookieFile = await this.cookieSnapshot(siteForUrl(spec.url));
      try {
        const proxy = await this.effectiveProxy(spec.url, spec.settings?.proxy);
        if (cancelled) { const error = new Error('操作已取消'); error.cancelled = true; throw error; }
        task = this.track(pythonTask({ ...spec, settings: { ...spec.settings, proxy }, cookieFile, userAgent: this.userAgent }, onEvent));
        return await task.promise;
      } finally { fs.unlinkSync(cookieFile); }
    })();
    return this.track(handle);
  }
  async readPage(script) {
    let timer, stopped = false;
    const wrapped = `(() => { if (document.readyState === 'loading') return {waiting: true};
      try { return {value: (${script})}; } catch (error) { return {error: String(error)}; } })()`;
    const read = async () => {
      while (!stopped) {
        let result;
        try { result = await this.browser.webContents.mainFrame.executeJavaScript(wrapped); }
        catch { await new Promise(resolve => setTimeout(resolve, 150)); continue; }
        if (result?.error) throw new Error(result.error);
        if (!result?.waiting) return result?.value;
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
  async inspect() {
    const url = this.browser.webContents.getURL();
    if (!contentKey(url)) throw new Error('请先在左侧打开 YouTube 视频、B站视频或小红书笔记详情页');
    if (siteForUrl(url) === 'bilibili' && this.documentKey !== contentKey(url)) throw new Error('播放器正在切换内容，请稍后重新解析');
    this.parseTask?.cancel(); const ticket = ++this.sequence;
    Object.assign(this.state, { parsing: true, result: null, inspectionId: null, error: '', logs: [] }); this.publish();
    let context = null;
    try {
      if (siteForUrl(url) === 'xiaohongshu') context = await this.readPage(snapshotScript);
      if (siteForUrl(url) === 'bilibili') context = await this.readPage(biliSnapshotScript);
      if (ticket !== this.sequence || this.closing) return;
      this.parseTask = this.withCookies({ command: 'inspect', url, context, settings: { ...this.settings } },
        (type, data) => { if (ticket === this.sequence && type === 'log') this.log(data); });
      const result = await this.parseTask.promise;
      if (ticket !== this.sequence || this.closing) return;
      const id = randomUUID(); this.cache.set(id, { result, context, url });
      if (this.cache.size > 30) this.cache.delete(this.cache.keys().next().value);
      Object.assign(this.state, { result, inspectionId: id, error: '' });
    } catch (error) {
      if (ticket === this.sequence && !error.cancelled) this.state.error = error.message.replace(/\u001b\[[0-9;]*m/g, '');
    } finally { if (ticket === this.sequence) { this.state.parsing = false; this.publish(); } }
  }
  download(data) {
    const snapshot = this.cache.get(data.inspectionId);
    if (!snapshot || data.inspectionId !== this.state.inspectionId) throw new Error('页面已切换，请重新解析后下载');
    const item = snapshot.result.items[Number(data.itemIndex)];
    if (!item || !siteForUrl(item.url)) throw new Error('资源不存在，请重新解析');
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
    let count = 0;
    for (const cookie of readCookies(fs.readFileSync(file, 'utf8'), site)) {
      try { await this.browserSession.cookies.set(cookie); count++; } catch {}
    }
    await this.browserSession.cookies.flushStore(); await this.updateLogin();
    if (!count) throw new Error('没有可导入的有效登录凭据，请重新登录');
    return count;
  }
  async login(site) {
    if (!SITES[site]) throw new Error('请先选择支持的站点');
    if (this.loginTask) throw new Error('已有登录窗口正在运行');
    const cookieFile = path.join(profile, 'tasks', randomUUID() + '.cookies.txt');
    this.state.loginRunning = site; this.publish();
    const proxy = await this.effectiveProxy(SITES[site].home, this.settings.proxy);
    this.loginTask = this.track(pythonTask({ command: 'login', site, cookieFile, proxy },
      (type, data) => { if (type === 'log') this.log(data); }));
    try { await this.loginTask.promise; await this.importCookies(cookieFile, site); this.log('登录凭据已同步，可重新解析当前内容'); }
    finally {
      this.loginTask = null; this.state.loginRunning = null;
      if (fs.existsSync(cookieFile)) fs.unlinkSync(cookieFile); this.publish();
    }
  }
  async invoke(method, data = {}) {
    if (method === 'state') return this.publicState();
    if (method === 'layout') return this.layout(data);
    if (method === 'navigate') return this.navigate(data.url);
    if (method === 'back') { if (this.browser.webContents.navigationHistory.canGoBack()) this.browser.webContents.navigationHistory.goBack(); return; }
    if (method === 'forward') { if (this.browser.webContents.navigationHistory.canGoForward()) this.browser.webContents.navigationHistory.goForward(); return; }
    if (method === 'reload') { this.browser.webContents.reload(); return; }
    if (method === 'inspect') return this.inspect();
    if (method === 'cancel-inspect') { this.invalidate(); this.publish(); return; }
    if (method === 'download') return this.download(data);
    if (method === 'cancel-task') return this.queue.cancel(String(data.id));
    if (method === 'retry-task') return this.queue.retry(String(data.id));
    if (method === 'login') return this.login(data.site);
    if (method === 'cancel-login') { this.loginTask?.cancel(); return; }
    if (method === 'import-login') {
      const site = data.site;
      if (!SITES[site]) throw new Error('请选择支持的站点');
      const file = path.join(os.homedir(), site === 'youtube' ? '.yt_dlp_gui_cookies.txt' : `.yt_dlp_gui_${site}_cookies.txt`);
      if (!fs.existsSync(file)) throw new Error('未找到旧版登录记录，请使用网页登录或 Edge 登录');
      return this.importCookies(file, site);
    }
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
    this.closing = true; this.invalidate(); this.queue.close(); this.handles.forEach(task => task.cancel());
    this.state.error = '正在停止后台任务并保存记录…'; this.publish();
    const finish = () => {
      if (this.handles.size) { setTimeout(finish, 150); return; }
      clearTimeout(this.saveTimer); this.persist();
      this.browser.webContents.close(); this.allowClose = true; this.window.close();
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
    catch (error) { return { ok: false, error: error.message }; }
  });
  await collector.create();
  if (smoke) await require('./smoke.cjs').run(collector, profile, snapshotScript);
  if (liveCheck) await require('./live-check.cjs').run(collector, profile);
  if (persistenceCheck) {
    await collector.healthTask.promise;
    const assert = require('node:assert/strict');
    assert.equal(collector.state.login.xiaohongshu, true);
    assert.equal(collector.settings.autoParse, false);
    assert.equal(collector.queue.tasks[0].status, 'completed');
    console.log('Restart persistence: browser login, settings and task history OK');
    collector.browser.webContents.close(); collector.window.destroy(); app.exit(0);
  }
}).catch(error => { console.error(error); app.exit(1); });
app.on('window-all-closed', () => app.quit());
module.exports = { CollectorApp, snapshotScript };
