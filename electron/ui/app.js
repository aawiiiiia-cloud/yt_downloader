'use strict';
const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const homes = { youtube: 'https://www.youtube.com/', bilibili: 'https://www.bilibili.com/', xiaohongshu: 'https://www.xiaohongshu.com/explore', vimeo: 'https://vimeo.com/', douyin: 'https://www.douyin.com/' };
const names = { youtube: 'YouTube', bilibili: 'B站', xiaohongshu: '小红书', vimeo: 'Vimeo', douyin: '抖音' };
let state, renderedId, selectedItem = 0, selectedSite = 'youtube', noticeTimer;
let settingsDirty = false;
const openedLogs = new Set();
const previews = new Map(), pendingDownloads = new Set();
const drafts = new Map();
let tabsSignature = '';
let previewObserver;
async function call(method, data) {
  try { return await window.collector.call(method, data); }
  catch (error) { notify(error.message, error.code === 'YOUTUBE_AUTH_REQUIRED'); }
}
function notify(message, login = false) { $('notice').textContent = message; $('notice').hidden = !message; $('required-login').hidden = !login; clearTimeout(noticeTimer); }
function rememberPreview(key, value) {
  previews.set(key, { ...previews.get(key), ...value });
  while (previews.size > 48 || [...previews.values()].reduce((sum, row) => sum + (row.dataUrl?.length || 0), 0) > 24000000) previews.delete(previews.keys().next().value);
}
function previewFrame(index, title) {
  return `<div class="preview-frame ${index === 'cover' ? 'cover-frame' : ''}" data-preview="${index}"><img data-preview-image alt="${escape(title)}" hidden><span class="preview-message">正在加载预览…</span><button type="button" class="preview-retry" data-retry-preview="${index}" hidden>重试</button></div>`;
}
function loadPreview(frame, refresh = false) {
  const receipt = state.inspectionId, itemIndex = selectedItem, imageIndex = frame.dataset.preview;
  const key = `${receipt}:${itemIndex}:${imageIndex}`;
  const current = () => frame.isConnected && state.inspectionId === receipt && selectedItem === itemIndex;
  const img = frame.querySelector('img'), message = frame.querySelector('.preview-message'), retry = frame.querySelector('button');
  const display = result => {
    if (!current()) return;
    img.onload = () => { if (current()) { img.hidden = false; message.hidden = true; retry.hidden = true; } };
    img.onerror = () => { if (current()) { img.hidden = true; message.hidden = false; message.textContent = '预览加载失败'; retry.hidden = false; } };
    img.src = result.dataUrl;
    if (img.complete && img.naturalWidth > 0) img.onload();
  };
  const args = { inspectionId: receipt, itemIndex, imageIndex: imageIndex === 'cover' ? null : Number(imageIndex), refresh };
  if (previews.get(key)?.dataUrl && !refresh) display(previews.get(key));
  else {
    message.hidden = false; message.textContent = '正在加载预览…'; retry.hidden = true;
    window.collector.call('preview', args).then(result => { rememberPreview(key, result); display(result); }, error => {
      if (current()) { img.hidden = true; message.hidden = false; message.textContent = error.message; retry.hidden = false; }
    });
  }
  if (imageIndex === 'cover') return;
  const label = frame.closest('.image-card').querySelector('[data-image-size]');
  const showSize = value => { if (current()) label.textContent = `${value.displayFallback ? '网页图' : '原图'} ${mediaInfo.size(value.size)}`; };
  if (previews.get(key)?.sizeLoaded) showSize(previews.get(key));
  else window.collector.call('image-size', args).then(value => {
    rememberPreview(key, { ...value, sizeLoaded: true }); showSize(value);
  }, () => { if (current()) label.textContent = '原图大小未知'; });
}
function updateMediaInfo() {
  const item = state?.result?.items?.[selectedItem];
  if (!item) return;
  if (item.kind === 'video' && $('video-size')) {
    const value = mediaInfo.videoSize(item, $('format-select')?.value, $('audio-only')?.checked);
    $('video-size').textContent = mediaInfo.size(value.size, value.estimated);
  }
  if ($('resource-size-note')) $('resource-size-note').textContent = item.kind === 'images' ?
    (state.settings.imageFormat === 'original' ? '预览使用网页展示图；大小按原始素材查询，下载优先原图。' : '图片显示源文件大小；格式转换后的大小以实际保存结果为准。') : '大小随画质更新，包含需要合并的音频；估算值以“约”标记。';
  const button = $('download');
  if (button) {
    button.disabled = pendingDownloads.has(state.inspectionId);
    button.textContent = button.disabled ? '正在处理…' : state.result.site === 'youtube' && state.youtubeAuth !== 'verified' ?
      (state.youtubeAuth === 'checking' ? '正在验证登录…' : state.login.youtube ? '验证登录并下载 ↓' : '通过 Edge 登录并下载 ↓') : '加入下载队列 ↓';
  }
}
function showPanel(name) {
  saveDraft();
  for (const panel of ['resources', 'tasks', 'settings']) {
    const active = panel === name;
    $(panel + '-panel').hidden = !active;
    $(panel + '-tab').classList.toggle('active', active);
    $(panel + '-tab').setAttribute('aria-selected', String(active));
  }
  if (name === 'resources') { restoreDraft(); updateMediaInfo(); restoreResourceScroll(); }
  if (name === 'settings' && !settingsDirty) fillSettings();
}
function syncLayout() {
  const r = $('browser-slot').getBoundingClientRect();
  call('layout', { x: Math.ceil(r.x), y: Math.ceil(r.y), width: Math.floor(r.width), height: Math.floor(r.height) });
}
function saveDraft() {
  if (!state?.activeTabId || !state.inspectionId || $('resources-panel').hidden) return;
  const old = drafts.get(state.activeTabId);
  const draft = old?.receipt === state.inspectionId ? old : { receipt: state.inspectionId, items: {} };
  draft.itemIndex = selectedItem; draft.scroll = $('resources-panel').scrollTop;
  draft.items[selectedItem] = {
    indices: [...document.querySelectorAll('[data-image]:checked')].map(input => Number(input.dataset.image)),
    format: $('format-select')?.value || '', audio: !!$('audio-only')?.checked, subtitles: !!$('subtitles')?.checked,
  };
  drafts.set(state.activeTabId, draft);
}
function restoreDraft() {
  const draft = drafts.get(state.activeTabId);
  if (draft?.receipt !== state.inspectionId) return;
  const item = draft.items[selectedItem];
  if (item) {
    document.querySelectorAll('[data-image]').forEach(input => { input.checked = item.indices.includes(Number(input.dataset.image)); });
    if ($('format-select')) { $('format-select').value = item.format; $('format-select').disabled = item.audio; }
    if ($('audio-only')) $('audio-only').checked = item.audio;
    if ($('subtitles')) $('subtitles').checked = item.subtitles;
  }
}
function restoreResourceScroll() {
  const draft = drafts.get(state.activeTabId);
  if (draft?.receipt === state.inspectionId) $('resources-panel').scrollTop = draft.scroll || 0;
}
function renderBrowserTabs() {
  const signature = JSON.stringify([state.activeGroup, state.activeTabId, state.tabs]);
  if (signature === tabsSignature) return;
  const activeChanged = !$('browser-tabs').querySelector(`[data-tab="${CSS.escape(state.activeTabId)}"][aria-selected="true"]`);
  tabsSignature = signature;
  $('browser-tabs').innerHTML = state.tabs.filter(tab => tab.group === state.activeGroup).map(tab => {
    const home = !tab.url || tab.url.replace(/\/$/, '') === homes[tab.group].replace(/\/$/, '');
    const title = home ? '首页' : tab.title || '新标签页';
    return `<div class="browser-tab ${tab.id === state.activeTabId ? 'active' : ''}"><button role="tab" data-tab="${escape(tab.id)}" aria-selected="${tab.id === state.activeTabId}" title="${escape(title)}">${tab.loading ? '◌ ' : ''}${escape(title)}</button><button class="close-tab" data-close-tab="${escape(tab.id)}" title="关闭 ${escape(title)}" aria-label="关闭 ${escape(title)}">×</button></div>`;
  }).join('');
  if (activeChanged) $('browser-tabs').querySelector('[aria-selected=true]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  document.querySelectorAll('[data-site-count]').forEach(label => { label.textContent = state.tabs.filter(tab => tab.group === label.dataset.siteCount).length; });
  for (const key of drafts.keys()) if (!state.tabs.some(tab => tab.id === key)) drafts.delete(key);
}
function renderResources() {
  previewObserver?.disconnect();
  const items = state.result?.items || [], item = items[selectedItem];
  if (!item) {
    $('resources').innerHTML = `<div class="empty"><div class="empty-icon">${state.parsing ? '◌' : '▧'}</div><strong>${state.parsing ? '正在识别当前内容…' : '等待值得收藏的内容'}</strong><p>${state.parsing ? '正在获取资源与实际可用画质，<br>你可以继续浏览或查看下载任务。' : '在左侧打开视频或笔记详情页，<br>或点击「解析当前页面」。'}</p></div>`;
    return;
  }
  const parts = items.length > 1 ? `<select id="part-select" class="part-select" aria-label="视频条目">${items.map((row, index) => `<option value="${index}" ${index === selectedItem ? 'selected' : ''}>${index + 1}. ${escape(row.title)}</option>`).join('')}</select>` : '';
  let html = parts + `<h2 class="content-title">${escape(item.title)}</h2><div class="author">${escape(item.author)}</div>`;
  if (item.parts?.length > 1) html += `<label class="muted">选择分P<select id="bili-part" class="part-select">${item.parts.map(p => `<option value="${p.index}" ${p.index === item.currentPart ? 'selected' : ''}>P${p.index} · ${escape(p.title)}</option>`).join('')}</select></label>`;
  if (item.kind === 'images') {
    html += `<div class="section-row"><span class="muted">图文 · ${item.images.length} 张图片</span><button id="select-all" class="text-button">全选 / 取消全选</button></div><div class="image-grid">${item.images.map(image => `<label class="image-card"><input type="checkbox" data-image="${image.index}" checked>${previewFrame(image.index, '第 ' + image.index + ' 张图片')}<span class="image-meta"><span>${String(image.index).padStart(2, '0')}</span><span>${image.width && image.height ? image.width + ' × ' + image.height : '原图优先'}</span></span><span class="image-size" data-image-size>正在查询原图大小…</span></label>`).join('')}</div>`;
  } else {
    html += `${previewFrame('cover', '视频封面')}<div class="media-summary"><span>时长 ${mediaInfo.duration(item.duration)}</span><span id="video-size">大小未知</span></div><div class="resource-options"><label>下载画质<select id="format-select"><option value="">最佳可用画质（自动合并音频）</option>${[...(item.formats || [])].reverse().filter(f => f.video).map(f => `<option value="${escape(f.id)}">${escape(f.height ? f.height + 'p' : f.note || f.id)}${f.fps ? ' · ' + f.fps + 'fps' : ''} · ${escape(f.ext)} · ${escape(f.id)}</option>`).join('')}</select></label><label class="check"><input id="audio-only" type="checkbox">仅下载音频（MP3）</label><label class="check"><input id="subtitles" type="checkbox">同时下载可用的中英文字幕</label></div>`;
  }
  $('resources').innerHTML = html + '<button id="download" class="primary download-action">加入下载队列 ↓</button><p class="muted" id="resource-size-note"></p>';
  restoreDraft(); updateMediaInfo(); restoreResourceScroll();
  previewObserver = new IntersectionObserver(entries => entries.forEach(entry => {
    if (entry.isIntersecting) { previewObserver.unobserve(entry.target); loadPreview(entry.target); }
  }), { root: $('resources-panel'), rootMargin: '100px' });
  $('resources').querySelectorAll('[data-preview]').forEach(frame => previewObserver.observe(frame));
}
function renderTasks() {
  const labels = { queued: '等待下载', running: '下载中', cancelling: '正在停止', cancelled: '已取消', completed: '已完成', failed: '下载失败' };
  $('tasks').innerHTML = state.tasks.length ? state.tasks.map(task => `<article class="task-card"><div class="task-title">${escape(task.title)}</div><div class="task-meta"><span>${labels[task.status] || escape(task.status)}${task.detail === 'processing' ? ' · 正在合并或转码' : ''}</span><span>${Math.round(task.percent || 0)}%</span></div><progress max="100" value="${task.percent || 0}"></progress>${task.error ? `<p class="task-error">${escape(task.error)}</p>` : ''}<div class="task-actions">${['queued', 'running'].includes(task.status) ? `<button data-cancel="${task.id}">取消</button>` : ''}${['failed', 'cancelled'].includes(task.status) && task.canRetry ? `<button data-retry="${task.id}">重试</button>` : ''}${task.status === 'completed' ? `<button data-folder="${task.id}">打开目录 ↗</button>` : ''}</div><details data-log="${task.id}" ${openedLogs.has(task.id) ? 'open' : ''}><summary>任务日志</summary><pre>${escape((task.logs || []).join('\n'))}</pre></details></article>`).join('') : '<div class="empty"><strong>下载队列还是空的</strong><p>在资源页选择图片或视频，<br>加入队列后可以在这里查看进度。</p></div>';
}
function render(next) {
  const previousError = state?.error;
  saveDraft();
  state = next;
  window.collectorLayout.load(state.settings.panelWidth);
  selectedSite = state.activeGroup;
  renderBrowserTabs();
  if (document.activeElement !== $('address')) $('address').value = /^https?:/.test(state.url) ? state.url : '';
  $('page-status').textContent = state.loading ? '网页加载中…' : state.title || '开始浏览';
  $('site-label').textContent = names[state.site] || '通用浏览';
  $('parse').textContent = state.parsing ? '停止解析' : '解析当前页面';
  $('retained-result-note').hidden = !state.inspectionId || state.resultMatchesPage;
  document.querySelectorAll('[data-site]').forEach(button => button.classList.toggle('active', button.dataset.site === state.activeGroup));
  const youtubeLabels = { signed_out: '未登录，下载前请通过 Edge 登录', unverified: '登录待验证', checking: '正在验证登录…', verified: '已登录并验证', expired: '登录已失效，请通过 Edge 更新' };
  $('login-state').textContent = selectedSite === 'youtube' ? `YouTube · ${youtubeLabels[state.youtubeAuth] || '登录待验证'}` : state.login[selectedSite] ? `${names[selectedSite]} · 已检测到登录凭据` : `${names[selectedSite]} · 尚未检测到登录凭据`;
  const loginBusy = !!state.loginRunning || !!state.importingCookies;
  $('web-login').hidden = selectedSite === 'youtube';
  document.querySelector('.login-tools').classList.toggle('youtube', selectedSite === 'youtube');
  $('web-login').disabled = loginBusy;
  document.querySelector('.login-help').textContent = (selectedSite === 'youtube' ? '通过 Edge 登录，下载前会验证。' : '可在左侧网页登录，或通过 Edge 登录。') + '导入请选择本站点的 Netscape 格式 Cookies 文件（.txt）。';
  $('edge-login').disabled = loginBusy; $('cancel-login').hidden = !state.loginRunning;
  $('import-login').disabled = loginBusy;
  $('import-login').textContent = state.importingCookies ? '正在导入…' : '导入Cookies';
  $('parse-log').textContent = state.logs.join('\n');
  const id = `${state.activeTabId}:${state.inspectionId}:${state.parsing}`;
  if (id !== renderedId) {
    const draft = drafts.get(state.activeTabId);
    selectedItem = draft?.receipt === state.inspectionId ? draft.itemIndex : 0;
    if (!state.result?.items[selectedItem]) selectedItem = 0;
    renderedId = id; renderResources();
  }
  updateMediaInfo();
  if (state.error) notify(state.error);
  else if (previousError && $('notice').textContent === previousError) notify('');
  $('task-count').textContent = state.tasks.filter(t => ['running', 'queued', 'cancelling'].includes(t.status)).length;
  renderTasks();
  if (!settingsDirty) fillSettings();
  const h = state.health;
  $('core-status').textContent = h ? (!h.ffmpeg ? '视频合并工具缺失，请查看设置' : '下载核心已连接 · 本地保存') : '正在连接下载核心…';
  $('health-detail').textContent = h ? `视频合并：${h.ffmpeg ? '就绪' : '缺失'}；YouTube 解算：${h.node ? '就绪' : '缺少 Node'}；YouTube 增强：${h.provider ? '就绪' : '未就绪，部分画质可能不可用'}` : '正在检查运行环境…';
}
function fillSettings() {
  if (!state) return;
  $('save-dir').value = state.settings.saveDir; $('proxy').value = state.settings.proxy;
  $('image-format').value = state.settings.imageFormat; $('auto-parse').checked = state.settings.autoParse;
}
$('address-form').addEventListener('submit', event => { event.preventDefault(); notify(''); call('navigate', { url: $('address').value }); });
for (const action of ['back', 'forward', 'reload']) $(action).addEventListener('click', () => call(action));
$('parse').addEventListener('click', () => { notify(''); call(state?.parsing ? 'cancel-inspect' : 'inspect'); });
document.querySelectorAll('[data-site]').forEach(button => button.addEventListener('click', () => { notify(''); call('switch-site', { site: button.dataset.site }); }));
$('new-tab').addEventListener('click', () => call('new-tab'));
$('browser-tabs').addEventListener('click', event => {
  const button = event.target.closest('button');
  if (button?.dataset.closeTab) call('close-tab', { id: button.dataset.closeTab });
  else if (button?.dataset.tab) call('activate-tab', { id: button.dataset.tab });
});
for (const panel of ['resources', 'tasks', 'settings']) $(panel + '-tab').addEventListener('click', () => showPanel(panel));
$('settings-panel').addEventListener('input', event => { if (event.target.matches('input:not([readonly]),select')) settingsDirty = true; });
$('settings-panel').addEventListener('change', event => { if (event.target.matches('input:not([readonly]),select')) settingsDirty = true; });
$('choose-folder').addEventListener('click', async () => { await call('choose-folder'); $('save-dir').value = state.settings.saveDir; });
$('open-folder').addEventListener('click', () => call('open-folder'));
$('save-settings').addEventListener('click', async () => {
  try {
    await window.collector.call('settings', { proxy: $('proxy').value, imageFormat: $('image-format').value, autoParse: $('auto-parse').checked });
    settingsDirty = false; notify('设置已保存。');
  } catch (error) { notify(error.message); }
});
$('edge-login').addEventListener('click', () => call('login', { site: selectedSite }));
$('import-login').addEventListener('click', async () => {
  const site = selectedSite;
  notify('');
  const count = await call('import-cookies', { site });
  if (count) notify(`${names[site]} Cookies 已导入（${count} 条），可重新解析当前页面。`);
});
$('cancel-login').addEventListener('click', () => call('cancel-login'));
$('web-login').addEventListener('click', () => selectedSite === 'youtube' ? call('login', { site: 'youtube' }) : call('navigate', { url: selectedSite === 'bilibili' ? 'https://passport.bilibili.com/login' : selectedSite === 'vimeo' ? 'https://vimeo.com/log_in' : homes[selectedSite] }));
$('required-login').addEventListener('click', () => call('login', { site: 'youtube' }));
$('resources').addEventListener('change', event => {
  if (event.target.id === 'part-select') { saveDraft(); selectedItem = Number(event.target.value); renderResources(); }
  if (event.target.id === 'bili-part') {
    const part = state.result.items[selectedItem].parts.find(p => p.index === Number(event.target.value));
    if (part) call('navigate', { url: part.url });
  }
  if (event.target.id === 'audio-only') $('format-select').disabled = event.target.checked;
  updateMediaInfo();
});
$('resources').addEventListener('click', async event => {
  if (event.target.dataset.retryPreview) { event.preventDefault(); loadPreview(event.target.closest('[data-preview]'), true); }
  if (event.target.id === 'select-all') { const inputs = [...document.querySelectorAll('[data-image]')], all = inputs.every(i => i.checked); inputs.forEach(i => { i.checked = !all; }); }
  if (event.target.id === 'download') {
    const args = { inspectionId: state.inspectionId, itemIndex: selectedItem,
      indices: [...document.querySelectorAll('[data-image]:checked')].map(i => Number(i.dataset.image)),
      formatId: $('format-select')?.value || '', audioOnly: $('audio-only')?.checked, subtitles: $('subtitles')?.checked };
    if (pendingDownloads.has(args.inspectionId)) return;
    pendingDownloads.add(args.inspectionId); updateMediaInfo();
    try {
      let id;
      try { id = await window.collector.call('download', args); }
      catch (error) {
        if (error.code !== 'YOUTUBE_AUTH_REQUIRED' || state.inspectionId !== args.inspectionId) throw error;
        notify('请在 Edge 中完成 YouTube 登录，完成后继续下载。');
        await window.collector.call('login', { site: 'youtube' });
        id = await window.collector.call('download', args);
      }
      if (id && state.inspectionId === args.inspectionId) { notify(''); showPanel('tasks'); }
    } catch (error) { notify(error.message, error.code === 'YOUTUBE_AUTH_REQUIRED'); }
    finally { pendingDownloads.delete(args.inspectionId); updateMediaInfo(); }
  }
});
$('tasks').addEventListener('click', event => {
  if (event.target.dataset.cancel) call('cancel-task', { id: event.target.dataset.cancel });
  if (event.target.dataset.retry) call('retry-task', { id: event.target.dataset.retry });
  if (event.target.dataset.folder) call('open-folder', { id: event.target.dataset.folder });
});
$('tasks').addEventListener('toggle', event => { if (event.target.dataset.log) { const id = event.target.dataset.log; if (event.target.open) openedLogs.add(id); else openedLogs.delete(id); } }, true);
new ResizeObserver(syncLayout).observe($('browser-slot'));
window.collector.onState(render); call('state').then(value => { if (value) render(value); syncLayout(); });
