'use strict';
const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const homes = { youtube: 'https://www.youtube.com/', bilibili: 'https://www.bilibili.com/', xiaohongshu: 'https://www.xiaohongshu.com/explore', vimeo: 'https://vimeo.com/', douyin: 'https://www.douyin.com/', behance: 'https://www.behance.net/', taobao: 'https://www.taobao.com/', zcool: 'https://www.zcool.com.cn/' };
const names = { youtube: 'YouTube', bilibili: 'B站', xiaohongshu: '小红书', vimeo: 'Vimeo', douyin: '抖音', behance: 'Behance', taobao: '淘宝 / 天猫', zcool: '站酷' };
let state, renderedId, selectedItem = 0, selectedSite = 'youtube', noticeTimer;
let settingsDirty = false;
const openedLogs = new Set();
const folderErrors = new Map(), openingFolders = new Set();
let clearingHistory = false;
const previews = new Map(), pendingDownloads = new Map();
const drafts = new Map();
let tabsSignature = '';
let previewObserver;
function setLoading(element, loading) {
  element.classList.toggle('is-loading', !!loading);
  element.setAttribute('aria-busy', String(!!loading));
  if (!loading) element.style.removeProperty('--spinner-phase');
  else alignLoadingAnimation(element);
}
function alignLoadingAnimation(element) {
  // Keep the same rotation phase when progress updates replace a task's markup.
  if (!element.style.getPropertyValue('--spinner-phase'))
    element.style.setProperty('--spinner-phase', `${-(performance.now() % 800)}ms`);
}
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
  return `<div class="preview-frame ${index === 'cover' ? 'cover-frame' : ''}" data-preview="${index}"><img data-preview-image alt="${escape(title)}" hidden><span class="preview-message is-loading" aria-busy="true">正在加载预览…</span><button type="button" class="preview-retry" data-retry-preview="${index}" hidden>重试</button></div>`;
}
function loadPreview(frame, refresh = false) {
  const receipt = state.inspectionId, itemIndex = selectedItem, imageIndex = frame.dataset.preview;
  const key = `${receipt}:${itemIndex}:${imageIndex}`;
  const generation = String((Number(frame.dataset.previewGeneration) || 0) + 1);
  frame.dataset.previewGeneration = generation;
  const current = () => frame.isConnected && state.inspectionId === receipt && selectedItem === itemIndex && frame.dataset.previewGeneration === generation;
  const image = state.result?.items?.[itemIndex]?.images?.find(image => image.index === Number(imageIndex));
  const needDimensions = state.result?.site === 'zcool' || (state.result?.site === 'taobao' && !(image?.width && image?.height));
  const complete = value => value.size > 0 && (!needDimensions || (value.width && value.height));
  let previewFailed = false, metadataIncomplete = false;
  const img = frame.querySelector('img'), message = frame.querySelector('.preview-message'), retry = frame.querySelector('button');
  const updateRetry = () => { retry.hidden = !(previewFailed || metadataIncomplete); };
  img.hidden = true; message.hidden = false; message.textContent = '正在加载预览…'; retry.hidden = true;
  setLoading(message, true);
  const display = result => {
    if (!current()) return;
    img.onload = () => { if (current()) { img.hidden = false; message.hidden = true; setLoading(message, false); previewFailed = false; updateRetry(); } };
    img.onerror = () => { if (current()) { img.hidden = true; message.hidden = false; setLoading(message, false); message.textContent = '预览加载失败'; previewFailed = true; updateRetry(); } };
    img.src = result.dataUrl;
    if (img.complete && img.naturalWidth > 0) img.onload();
  };
  const args = { inspectionId: receipt, itemIndex, imageIndex: imageIndex === 'cover' ? null : Number(imageIndex), refresh };
  if (previews.get(key)?.dataUrl && !refresh) display(previews.get(key));
  else {
    window.collector.call('preview', args).then(result => { if (current()) { rememberPreview(key, result); display(result); } }, error => {
      if (current()) { img.hidden = true; message.hidden = false; setLoading(message, false); message.textContent = error.message; previewFailed = true; updateRetry(); }
    });
  }
  if (imageIndex === 'cover') return;
  const label = frame.closest('.image-card').querySelector('[data-image-size]');
  const showSize = value => {
    if (!current()) return;
    setLoading(label, false);
    label.textContent = '大小：' + (value.size > 0 ? mediaInfo.size(value.size).replace(' MB', 'MB') : '未知');
    label.title = value.displayFallback ? '网页展示图片的大小' : '源图片大小';
    if (value.width && value.height) frame.closest('.image-card').querySelector('[data-image-dimensions]').textContent = `尺寸：${value.width}×${value.height}`;
    metadataIncomplete = !complete(value); updateRetry();
  };
  const cached = previews.get(key);
  if (!refresh && cached?.sizeLoaded && (complete(cached) || Date.now() - cached.metadataAt < 5000)) showSize(cached);
  else {
    setLoading(label, true);
    window.collector.call('image-size', args).then(value => {
      if (current()) { rememberPreview(key, { ...value, sizeLoaded: true, metadataAt: Date.now() }); showSize(value); }
    }, () => { if (current()) { setLoading(label, false); label.textContent = '大小：未知'; label.title = '图片大小未知'; metadataIncomplete = true; updateRetry(); } });
  }
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
  if ($('resource-size-note') && state.result.site === 'behance' && item.kind === 'images' && state.settings.imageFormat === 'original')
    $('resource-size-note').textContent += ' GIF 优先使用静帧预览，保存保留动画。';
  if ($('resource-size-note') && state.result.site === 'zcool' && item.kind === 'images')
    $('resource-size-note').textContent = '尺寸和大小按下载地址查询；下载时核对实际宽高，低于作品标注尺寸的缩放图不保存。';
  const button = $('download');
  const pendingKey = index => JSON.stringify([state.inspectionId, selectedItem, index]);
  document.querySelectorAll('[data-download-image]').forEach(button => {
    button.disabled = pendingDownloads.has(pendingKey(Number(button.dataset.downloadImage)));
    setLoading(button, button.disabled);
  });
  if (button) {
    const checkingYouTube = state.result.site === 'youtube' && state.youtubeAuth === 'checking';
    button.disabled = pendingDownloads.has(pendingKey(null));
    button.textContent = button.disabled ? '正在处理…' : checkingYouTube ? '正在验证登录…' : '下载所选';
    setLoading(button, button.disabled || checkingYouTube);
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
    return `<div class="browser-tab ${tab.id === state.activeTabId ? 'active' : ''}"><button role="tab" data-tab="${escape(tab.id)}" class="${tab.loading ? 'is-loading' : ''}" aria-busy="${!!tab.loading}" aria-selected="${tab.id === state.activeTabId}" title="${escape(title)}">${escape(title)}</button><button class="close-tab" data-close-tab="${escape(tab.id)}" title="关闭 ${escape(title)}" aria-label="关闭 ${escape(title)}">×</button></div>`;
  }).join('');
  if (activeChanged) $('browser-tabs').querySelector('[aria-selected=true]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  document.querySelectorAll('[data-site-count]').forEach(label => { label.textContent = state.tabs.filter(tab => tab.group === label.dataset.siteCount).length; });
  for (const key of drafts.keys()) if (!state.tabs.some(tab => tab.id === key)) drafts.delete(key);
}
function renderResources() {
  previewObserver?.disconnect();
  const items = state.result?.items || [], item = items[selectedItem];
  const skipped = state.result?.site === 'behance' ? state.result.skippedExternal || 0 : 0;
  const externalTools=state.result?.site==='behance'&&(skipped||state.result.externalParsed)?
    `<div class="section-row" id="external-controls"><span class="muted" id="skipped-external">${state.result.externalParsed?`已识别 ${state.result.externalCount} 个外部视频。${skipped?`另有 ${skipped} 个外部视频未解析。`:''}`:`已跳过 ${skipped} 个外部托管视频。`}</span><button id="inspect-external" class="text-button ${state.parsing?'is-loading':''}" aria-busy="${!!state.parsing}" ${state.parsing||!state.resultMatchesPage?'disabled':''}>${state.parsing?'正在解析…':state.result.externalParsed?'重新解析外部视频':'解析外部视频'}</button></div>`:'';
  if (!item) {
    if (skipped) {
      $('resources').innerHTML = externalTools+`<div class="empty"><strong>当前作品没有本站可下载素材</strong><p>可手动解析 YouTube、Vimeo 托管的视频。</p></div>`;
      return;
    }
    $('resources').innerHTML = `<div class="empty" aria-busy="${!!state.parsing}"><div class="empty-icon ${state.parsing ? 'is-loading' : ''}" aria-hidden="true">${state.parsing ? '' : '▧'}</div><strong>${state.parsing ? '正在识别当前内容…' : '等待值得收藏的内容'}</strong><p>${state.parsing ? '正在获取资源与实际可用画质，<br>你可以继续浏览或查看下载任务。' : '在左侧打开视频、笔记、作品或商品详情页，<br>或点击「解析当前页面」。'}</p></div>`;
    return;
  }
  const commerce = state.result.site === 'taobao';
  const parts = commerce ? `<div class="image-categories" role="tablist" aria-label="商品图片分类">${Object.entries({main:'主图',sku:'SKU 图',detail:'详情图'}).map(([key,label]) => {
    const index = items.findIndex(row => row.category === key), count = items[index]?.images?.length || 0;
    return `<button type="button" data-image-category="${index}" role="tab" aria-selected="${index === selectedItem}" ${index < 0 ? 'disabled' : ''}>${label} ${count}</button>`;
  }).join('')}</div>` : items.length > 1 ? `<select id="part-select" class="part-select" aria-label="资源条目">${items.map((row, index) => `<option value="${index}" ${index === selectedItem ? 'selected' : ''}>${index + 1}. ${row.kind === 'images' ? '图片集 · ' : ''}${escape(row.title)}${row.provider ? ' · ' + escape(row.provider) : ''}</option>`).join('')}</select>` : '';
  const warnings = commerce || state.result.site === 'zcool' ? (state.result.warnings || [])
    .filter(message => item.kind === 'images' || !(state.result.imageWarnings || []).includes(message))
    .map(message => `<p class="muted collection-notice">${escape(message)}</p>`).join('') : '';
  let html = externalTools+parts+warnings + `<h2 class="content-title">${escape(item.title)}</h2><div class="author">${item.externalId?escape(item.provider)+' · 外部托管 · ':''}${escape(item.author)}</div>`;
  if (['external', 'unavailable'].includes(item.kind)) {
    $('resources').innerHTML = html + `<p class="muted">${escape(item.kind === 'external' ? '外部平台托管的视频默认不下载。' : item.message)}</p>`;
    return;
  }
  if (item.parts?.length > 1) html += `<label class="muted">选择分P<select id="bili-part" class="part-select">${item.parts.map(p => `<option value="${p.index}" ${p.index === item.currentPart ? 'selected' : ''}>P${p.index} · ${escape(p.title)}</option>`).join('')}</select></label>`;
  if (item.kind === 'images') {
    html += `<div class="section-row"><span class="muted">图文 · ${item.images.length} 张图片</span><button id="select-all" class="text-button">全选 / 取消全选</button></div><div class="image-grid">${item.images.map(image => `<div class="image-card"><label class="image-select"><input type="checkbox" id="image-select-${image.index}" data-image="${image.index}" aria-label="选择第 ${image.index} 张图片" checked>${previewFrame(image.index, '第 ' + image.index + ' 张图片')}</label><div class="image-footer"><label class="image-meta" for="image-select-${image.index}"><span>${String(image.index).padStart(2, '0')}</span><span data-image-dimensions>尺寸：${image.width && image.height ? image.width + '×' + image.height : '未知'}</span><span class="image-size is-loading" data-image-size aria-busy="true" title="正在读取图片大小">大小：未知</span>${image.names?.length ? `<span class="image-name" data-image-name="${image.index}"></span>` : ""}</label><button type="button" class="image-download" data-download-image="${image.index}" aria-label="下载第 ${image.index} 张图片">下载</button></div></div>`).join('')}</div>`;
  } else {
    html += `${previewFrame('cover', '视频封面')}<div class="media-summary"><span>时长 ${mediaInfo.duration(item.duration)}</span><span id="video-size">大小未知</span></div><div class="resource-options"><label>下载画质<select id="format-select"><option value="">最佳可用画质（自动合并音频）</option>${[...(item.formats || [])].reverse().filter(f => f.video).map(f => `<option value="${escape(f.id)}">${escape(f.height ? f.height + 'p' : f.note || f.id)}${f.fps ? ' · ' + f.fps + 'fps' : ''} · ${escape(f.ext)} · ${escape(f.id)}</option>`).join('')}</select></label><label class="check"><input id="audio-only" type="checkbox">仅下载音频（MP3）</label><label class="check"><input id="subtitles" type="checkbox">同时下载可用的中英文字幕</label></div>`;
  }
  $('resources').innerHTML = html + '<button id="download" class="primary download-action">下载所选</button><p class="muted" id="resource-size-note"></p>';
  restoreDraft(); updateMediaInfo(); restoreResourceScroll();
  previewObserver = new IntersectionObserver(entries => entries.forEach(entry => {
    if (entry.isIntersecting) { previewObserver.unobserve(entry.target); loadPreview(entry.target); }
  }), { root: $('resources-panel'), rootMargin: '600px 0px' });
  $('resources').querySelectorAll('[data-preview]').forEach(frame => previewObserver.observe(frame));
  document.querySelectorAll('[data-image-name]').forEach(label => {
    const image = item.images?.find(row => row.index === Number(label.dataset.imageName));
    label.textContent = (image?.names || []).join('、');
  });
}
function renderTasks() {
  const labels = { queued: '等待下载', running: '下载中', cancelling: '正在停止', cancelled: '已取消', completed: '已完成', failed: '下载失败' };
  const ids = new Set(state.tasks.map(task => task.id));
  for (const id of folderErrors.keys()) if (!ids.has(id)) folderErrors.delete(id);
  for (const id of openedLogs) if (!ids.has(id)) openedLogs.delete(id);
  $('clear-history').disabled = clearingHistory || !state.tasks.some(task => ['completed', 'failed', 'cancelled'].includes(task.status));
  $('tasks').innerHTML = state.tasks.length ? state.tasks.map(task => {
    const error = task.status === 'failed' ? `<p class="task-error"><strong>失败原因：${escape(task.failure?.label || '原因未确定')}</strong><span>${escape(task.error || '请展开任务日志查看详情。')}</span></p>` : task.error ? `<p class="task-error">${escape(task.error)}</p>` : '';
    const progress = window.collectorTaskProgress(task);
    return `<article class="task-card" data-task-id="${escape(task.id)}"><div class="task-heading"><div class="task-title">${escape(task.title)}</div><span class="task-speed" data-task-speed="${escape(task.id)}" title="下载接收速度；等待、校验和转码阶段不显示旧速度"${['running', 'cancelling'].includes(task.status) ? '' : ' hidden'}>${escape(window.collectorTaskSpeed(task))}</span></div><div class="task-meta"><span class="${['running','cancelling'].includes(task.status)?'is-loading':''}" aria-busy="${['running','cancelling'].includes(task.status)}">${labels[task.status] || escape(task.status)}${progress.stage ? ' · ' + escape(progress.stage) : ''}</span><span>${escape(progress.text)}</span></div><progress max="100"${progress.attribute}></progress>${error}<div class="task-actions">${folderErrors.has(task.id) ? `<span class="task-folder-error" role="status">${escape(folderErrors.get(task.id))}</span>` : ''}${['queued', 'running'].includes(task.status) ? `<button data-cancel="${task.id}">取消</button>` : ''}${['failed', 'cancelled'].includes(task.status) && task.canRetry ? `<button data-retry="${task.id}">重试</button>` : ''}${task.status === 'completed' ? `<button data-folder="${escape(task.id)}"${openingFolders.has(task.id) ? ' disabled' : ''}>打开目录 ↗</button>` : ''}</div><details data-log="${task.id}" ${openedLogs.has(task.id) ? 'open' : ''}><summary>任务日志</summary><pre>${escape((task.logs || []).join('\n'))}</pre></details></article>`;
  }).join('') : '<div class="empty"><strong>下载队列还是空的</strong><p>在资源页选择图片或视频，<br>加入队列后可以在这里查看进度。</p></div>';
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
  setLoading($('page-status'), state.loading);
  $('site-label').textContent = names[state.site] || '通用浏览';
  $('parse').textContent = state.parsing ? '停止解析' : '解析当前页面';
  setLoading($('parse'), state.parsing);
  document.querySelectorAll('[data-site]').forEach(button => button.classList.toggle('active', button.dataset.site === state.activeGroup));
  const expired = state.loginExpired?.[selectedSite] || (selectedSite === 'youtube' && state.youtubeAuth === 'expired');
  const account = loginStatus.display(selectedSite, names[selectedSite], !!state.login[selectedSite], expired, state.youtubeAuth);
  $('login-state').textContent = account.text;
  $('login-state').classList.toggle('login-warning', account.warning);
  $('login-state').classList.toggle('login-success', account.success);
  $('login-state').title = account.title;
  const loginBusy = !!state.loginRunning || !!state.importingCookies || !!state.openingWebLogin;
  const externalLoginOnly = ['youtube', 'vimeo'].includes(selectedSite);
  $('web-login').hidden = externalLoginOnly;
  $('edge-login').hidden = ['behance', 'zcool'].includes(selectedSite);
  document.querySelector('.login-tools').classList.toggle('external-login-only', externalLoginOnly || ['behance', 'zcool'].includes(selectedSite));
  $('web-login').disabled = loginBusy;
  $('web-login').textContent = state.openingWebLogin === selectedSite ? '正在打开…' : '登录';
  setLoading($('web-login'), state.openingWebLogin === selectedSite);
  document.querySelector('.login-help').textContent = (selectedSite === 'youtube' ? '通过 Edge 登录，下载前会验证。' : externalLoginOnly ? '通过 Edge 登录。' : '可在左侧网页登录，或通过 Edge 登录。') + '也可选择本站的 Netscape 格式 Cookies 文件（.txt）。右侧显示的是凭据状态，网页是否已登录请以左侧为准。';
  if (selectedSite === 'behance') document.querySelector('.login-help').textContent = '需要登录的作品，请在左侧完成 Adobe 登录；也可导入 Behance Cookies。账号状态以左侧网页为准。';
  if (selectedSite === 'zcool') document.querySelector('.login-help').textContent = '高清图片建议先登录站酷，再刷新作品页并重新解析；也可导入本站 Cookies。右侧只确认凭据存在，是否有效以左侧网页和站酷验证为准。';
  $('edge-login').disabled = loginBusy; $('cancel-login').hidden = !state.loginRunning;
  $('import-login').disabled = loginBusy;
  $('import-login').textContent = state.importingCookies ? '正在导入…' : '导入Cookies';
  setLoading($('import-login'), state.importingCookies);
  $('edge-login').textContent = state.loginRunning === selectedSite ? '等待登录…' : 'Edge登录';
  setLoading($('edge-login'), state.loginRunning === selectedSite);
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
  setLoading($('core-status'), !h);
  document.querySelector('.status-dot').hidden = !h;
  $('health-detail').textContent = h ? `视频合并：${h.ffmpeg ? '就绪' : '缺失'}；YouTube 解算：${h.node ? '就绪' : '缺少 Node'}；YouTube 增强：${h.provider ? '就绪' : '未就绪，部分画质可能不可用'}；Behance 解析：${h.packages?.['gallery-dl'] ? '就绪' : '缺少 gallery-dl'}` : '正在检查运行环境…';
  setLoading($('health-detail'), !h);
  document.querySelectorAll('.is-loading').forEach(alignLoadingAnimation);
}
function fillSettings() {
  if (!state) return;
  $('save-dir').value = state.settings.saveDir; $('proxy').value = state.settings.proxy;
  $('image-format').value = state.settings.imageFormat; $('auto-parse').checked = state.settings.autoParse;
  $('global-concurrency').value = state.settings.globalConcurrency;
  $('site-concurrency').value = state.settings.siteConcurrency;
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
$('clear-history').addEventListener('click', async () => {
  if (clearingHistory) return;
  clearingHistory = true; renderTasks();
  try {
    const removed = await window.collector.call('clear-history');
    notify(removed ? `已清除 ${removed} 条已结束的任务记录。` : '暂无可清除的任务记录。');
  } catch (error) { notify(error.message); }
  finally { clearingHistory = false; renderTasks(); }
});
$('save-settings').addEventListener('click', async () => {
  try {
    for (const id of ['global-concurrency', 'site-concurrency']) {
      if (!$(id).checkValidity()) { $('advanced-options').open = true; $(id).reportValidity(); return; }
    }
    await window.collector.call('settings', { proxy: $('proxy').value, imageFormat: $('image-format').value, autoParse: $('auto-parse').checked,
      globalConcurrency: $('global-concurrency').valueAsNumber, siteConcurrency: $('site-concurrency').valueAsNumber });
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
$('web-login').addEventListener('click', () => call('web-login', { site: selectedSite }));
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
  const categoryButton = event.target.closest('[data-image-category]');
  if (categoryButton && !categoryButton.disabled) { saveDraft(); selectedItem = Number(categoryButton.dataset.imageCategory); renderResources(); return; }
  if(event.target.id==='inspect-external')return call('inspect-external',{inspectionId:state.inspectionId});
  if (event.target.dataset.retryPreview) { event.preventDefault(); loadPreview(event.target.closest('[data-preview]'), true); }
  if (event.target.id === 'select-all') { const inputs = [...document.querySelectorAll('[data-image]')], all = inputs.every(i => i.checked); inputs.forEach(i => { i.checked = !all; }); }
  const imageButton = event.target.closest('[data-download-image]');
  if (event.target.id === 'download' || imageButton) {
    event.preventDefault();
    const downloadOrigin = (imageButton || $('download')).getBoundingClientRect();
    const args = { inspectionId: state.inspectionId, itemIndex: selectedItem,
      indices: imageButton ? [Number(imageButton.dataset.downloadImage)] : [...document.querySelectorAll('[data-image]:checked')].map(i => Number(i.dataset.image)),
      formatId: $('format-select')?.value || '', audioOnly: $('audio-only')?.checked, subtitles: $('subtitles')?.checked };
    const requestKey = JSON.stringify([args.inspectionId, args.itemIndex, imageButton ? Number(imageButton.dataset.downloadImage) : null]);
    if (pendingDownloads.has(requestKey)) return;
    const requestToken = {};
    pendingDownloads.set(requestKey, requestToken); updateMediaInfo();
    const release = () => {
      // A previous animation finishing must not unlock a newer request.
      if (pendingDownloads.get(requestKey) === requestToken) { pendingDownloads.delete(requestKey); updateMediaInfo(); }
    };
    try {
      let id;
      try { id = await window.collector.call('download', args); }
      catch (error) {
        if (error.code !== 'YOUTUBE_AUTH_REQUIRED' || state.inspectionId !== args.inspectionId) throw error;
        notify('请在 Edge 中完成 YouTube 登录，完成后继续下载。');
        await window.collector.call('login', { site: 'youtube' });
        id = await window.collector.call('download', args);
      }
      if (id) {
        release();
        if (state.inspectionId === args.inspectionId)
          notify(imageButton ? `第 ${args.indices[0]} 张图片已加入下载队列。` : '已加入下载队列。');
        await window.collectorDownloadFeedback.fly(downloadOrigin, $('tasks-tab'));
      }
    } catch (error) { notify(error.message, error.code === 'YOUTUBE_AUTH_REQUIRED'); }
    finally { release(); }
  }
});
$('tasks').addEventListener('click', async event => {
  if (event.target.dataset.cancel) call('cancel-task', { id: event.target.dataset.cancel });
  if (event.target.dataset.retry) call('retry-task', { id: event.target.dataset.retry });
  if (event.target.dataset.folder) {
    const id = event.target.dataset.folder;
    if (openingFolders.has(id)) return;
    openingFolders.add(id); folderErrors.delete(id); renderTasks();
    try { await window.collector.call('open-folder', { id }); }
    catch (error) { if (state.tasks.some(task => task.id === id)) folderErrors.set(id, error.message); }
    finally { openingFolders.delete(id); renderTasks(); }
  }
});
$('tasks').addEventListener('toggle', event => { if (event.target.dataset.log) { const id = event.target.dataset.log; if (event.target.open) openedLogs.add(id); else openedLogs.delete(id); } }, true);
new ResizeObserver(syncLayout).observe($('browser-slot'));
// Refresh only the speed labels, not the entire card (keep log selection and focus intact).
const speedTimer = window.setInterval(() => {
  if (!state || $('tasks-panel').hidden) return;
  const tasks = new Map((state.tasks || []).map(task => [task.id, task]));
  document.querySelectorAll('[data-task-speed]').forEach(label => {
    const task = tasks.get(label.dataset.taskSpeed);
    if (task) label.textContent = window.collectorTaskSpeed(task);
  });
}, 1000);
window.addEventListener('pagehide', () => window.clearInterval(speedTimer));
window.collector.onState(render); call('state').then(value => { if (value) render(value); syncLayout(); });
