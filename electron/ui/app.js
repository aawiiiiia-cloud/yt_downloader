'use strict';
const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const homes = { youtube: 'https://www.youtube.com/', bilibili: 'https://www.bilibili.com/', xiaohongshu: 'https://www.xiaohongshu.com/explore' };
const names = { youtube: 'YouTube', bilibili: 'B站', xiaohongshu: '小红书' };
let state, renderedId, selectedItem = 0, selectedSite = 'youtube', noticeTimer;
const openedLogs = new Set();
const imageUrl = value => { try { const u = new URL(value); return u.protocol === 'https:' ? u.href : ''; } catch { return ''; } };
async function call(method, data) {
  try { return await window.collector.call(method, data); }
  catch (error) { notify(error.message); }
}
function notify(message) { $('notice').textContent = message; $('notice').hidden = !message; clearTimeout(noticeTimer); }
function showTab(tasks) {
  $('tasks-panel').hidden = !tasks; $('resources-panel').hidden = tasks;
  $('tasks-tab').classList.toggle('active', tasks); $('resources-tab').classList.toggle('active', !tasks);
}
function syncLayout() {
  const r = $('browser-slot').getBoundingClientRect();
  call('layout', { x: Math.ceil(r.x), y: Math.ceil(r.y), width: Math.floor(r.width), height: Math.floor(r.height) });
}
function renderResources() {
  const items = state.result?.items || [], item = items[selectedItem];
  if (!item) {
    $('resources').innerHTML = `<div class="empty"><div class="empty-icon">${state.parsing ? '◌' : '▧'}</div><strong>${state.parsing ? '正在识别当前内容…' : '等待值得收藏的内容'}</strong><p>${state.parsing ? '正在获取资源与实际可用画质，<br>你可以继续浏览或查看下载任务。' : '在左侧打开视频或笔记详情页，<br>或点击「解析当前页面」。'}</p></div>`;
    return;
  }
  const parts = items.length > 1 ? `<select id="part-select" class="part-select" aria-label="视频条目">${items.map((row, index) => `<option value="${index}" ${index === selectedItem ? 'selected' : ''}>${index + 1}. ${escape(row.title)}</option>`).join('')}</select>` : '';
  let html = parts + `<h2 class="content-title">${escape(item.title)}</h2><div class="author">${escape(item.author)}${item.duration ? ' · ' + Math.round(item.duration / 60) + ' 分钟' : ''}</div>`;
  if (item.parts?.length > 1) html += `<label class="muted">选择分P<select id="bili-part" class="part-select">${item.parts.map(p => `<option value="${p.index}" ${p.index === item.currentPart ? 'selected' : ''}>P${p.index} · ${escape(p.title)}</option>`).join('')}</select></label>`;
  if (item.kind === 'images') {
    html += `<div class="section-row"><span class="muted">图文 · ${item.images.length} 张图片</span><button id="select-all" class="text-button">全选 / 取消全选</button></div><div class="image-grid">${item.images.map(image => `<label class="image-card"><input type="checkbox" data-image="${image.index}" checked><img src="${escape(imageUrl(image.fallback_urls?.at(-1) || image.url))}" loading="lazy" alt="第 ${image.index} 张图片"><span class="image-meta"><span>${String(image.index).padStart(2, '0')}</span><span>${image.width && image.height ? image.width + ' × ' + image.height : '原图优先'}</span></span></label>`).join('')}</div>`;
  } else {
    html += `<img class="cover" src="${escape(imageUrl(item.thumbnail))}" alt="视频封面"><div class="resource-options"><label>下载画质<select id="format-select"><option value="">最佳可用画质（自动合并音频）</option>${[...(item.formats || [])].reverse().filter(f => f.video).map(f => `<option value="${escape(f.id)}">${escape(f.height ? f.height + 'p' : f.note || f.id)}${f.fps ? ' · ' + f.fps + 'fps' : ''} · ${escape(f.ext)} · ${escape(f.id)}</option>`).join('')}</select></label><label class="check"><input id="audio-only" type="checkbox">仅下载音频（MP3）</label><label class="check"><input id="subtitles" type="checkbox">同时下载可用的中英文字幕</label></div>`;
  }
  $('resources').innerHTML = html + '<button id="download" class="primary download-action">加入下载队列 ↓</button><p class="muted">实际保存结果以下载日志为准；网页浏览可继续。</p>';
  $('resources').querySelectorAll('img').forEach(img => img.addEventListener('error', () => { img.style.opacity = '.15'; }, { once: true }));
}
function renderTasks() {
  const labels = { queued: '等待下载', running: '下载中', cancelling: '正在停止', cancelled: '已取消', completed: '已完成', failed: '下载失败' };
  $('tasks').innerHTML = state.tasks.length ? state.tasks.map(task => `<article class="task-card"><div class="task-title">${escape(task.title)}</div><div class="task-meta"><span>${labels[task.status] || escape(task.status)}${task.detail === 'processing' ? ' · 正在合并或转码' : ''}</span><span>${Math.round(task.percent || 0)}%</span></div><progress max="100" value="${task.percent || 0}"></progress>${task.error ? `<p class="task-error">${escape(task.error)}</p>` : ''}<div class="task-actions">${['queued', 'running'].includes(task.status) ? `<button data-cancel="${task.id}">取消</button>` : ''}${['failed', 'cancelled'].includes(task.status) && task.canRetry ? `<button data-retry="${task.id}">重试</button>` : ''}${task.status === 'completed' ? `<button data-folder="${task.id}">打开目录 ↗</button>` : ''}</div><details data-log="${task.id}" ${openedLogs.has(task.id) ? 'open' : ''}><summary>任务日志</summary><pre>${escape((task.logs || []).join('\n'))}</pre></details></article>`).join('') : '<div class="empty"><strong>下载队列还是空的</strong><p>在资源页选择图片或视频，<br>加入队列后可以在这里查看进度。</p></div>';
}
function render(next) {
  const previousError = state?.error;
  state = next;
  if (state.site) selectedSite = state.site;
  if (document.activeElement !== $('address')) $('address').value = /^https?:/.test(state.url) ? state.url : '';
  $('page-status').textContent = state.loading ? '网页加载中…' : state.title || '开始浏览';
  $('site-label').textContent = names[state.site] || '通用浏览';
  $('parse').textContent = state.parsing ? '停止解析' : '解析当前页面';
  document.querySelectorAll('[data-site]').forEach(button => button.classList.toggle('active', button.dataset.site === state.site));
  $('login-state').textContent = state.login[selectedSite] ? `${names[selectedSite]} · 已检测到登录凭据` : `${names[selectedSite]} · 尚未检测到登录凭据`;
  $('edge-login').disabled = !!state.loginRunning; $('cancel-login').hidden = !state.loginRunning;
  $('parse-log').textContent = state.logs.join('\n');
  const id = `${state.inspectionId}:${state.parsing}`;
  if (id !== renderedId) { selectedItem = 0; renderedId = id; renderResources(); }
  if (state.error) notify(state.error);
  else if (previousError && $('notice').textContent === previousError) notify('');
  $('task-count').textContent = state.tasks.filter(t => ['running', 'queued', 'cancelling'].includes(t.status)).length;
  renderTasks();
  if ($('settings-panel').hidden) fillSettings();
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
document.querySelectorAll('[data-site]').forEach(button => button.addEventListener('click', () => { selectedSite = button.dataset.site; notify(''); call('navigate', { url: homes[selectedSite] }); }));
$('resources-tab').addEventListener('click', () => showTab(false)); $('tasks-tab').addEventListener('click', () => showTab(true));
$('settings-toggle').addEventListener('click', () => { $('settings-panel').hidden = !$('settings-panel').hidden; fillSettings(); });
$('choose-folder').addEventListener('click', async () => { await call('choose-folder'); fillSettings(); });
$('open-folder').addEventListener('click', () => call('open-folder'));
$('save-settings').addEventListener('click', async () => { await call('settings', { proxy: $('proxy').value, imageFormat: $('image-format').value, autoParse: $('auto-parse').checked }); });
$('edge-login').addEventListener('click', () => call('login', { site: selectedSite }));
$('import-login').addEventListener('click', async () => { const count = await call('import-login', { site: selectedSite }); if (count) notify('旧版登录已导入，可重新解析当前页面。'); });
$('cancel-login').addEventListener('click', () => call('cancel-login'));
$('web-login').addEventListener('click', () => call('navigate', { url: selectedSite === 'youtube' ? 'https://accounts.google.com/ServiceLogin?continue=https%3A%2F%2Fwww.youtube.com' : selectedSite === 'bilibili' ? 'https://passport.bilibili.com/login' : homes.xiaohongshu }));
$('resources').addEventListener('change', event => {
  if (event.target.id === 'part-select') { selectedItem = Number(event.target.value); renderResources(); }
  if (event.target.id === 'bili-part') {
    const part = state.result.items[selectedItem].parts.find(p => p.index === Number(event.target.value));
    if (part) call('navigate', { url: part.url });
  }
  if (event.target.id === 'audio-only') $('format-select').disabled = event.target.checked;
});
$('resources').addEventListener('click', async event => {
  if (event.target.id === 'select-all') { const inputs = [...document.querySelectorAll('[data-image]')], all = inputs.every(i => i.checked); inputs.forEach(i => { i.checked = !all; }); }
  if (event.target.id === 'download') {
    const id = await call('download', { inspectionId: state.inspectionId, itemIndex: selectedItem,
      indices: [...document.querySelectorAll('[data-image]:checked')].map(i => Number(i.dataset.image)),
      formatId: $('format-select')?.value || '', audioOnly: $('audio-only')?.checked, subtitles: $('subtitles')?.checked });
    if (id) showTab(true);
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
