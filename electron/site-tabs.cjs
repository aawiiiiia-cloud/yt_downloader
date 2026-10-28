'use strict';
const { randomUUID } = require('node:crypto');
const { SITES, navigationUrl, siteForUrl, contentKey } = require('./model.cjs');
const { isTaobaoLoginUrl } = require('./browser-popups.cjs');
const MAX_TABS = 36;

// Only navigation metadata is saved. Views, parsed resources and task handles live in memory.
class SiteTabs {
  constructor(saved) {
    this.rows = []; this.activeId = null; this.lastActive = {};
    for (const row of (Array.isArray(saved?.tabs) ? saved.tabs : []).slice(0, MAX_TABS)) {
      if (!SITES[row?.group] || isTaobaoLoginUrl(row.url)) continue;
      try {
        const tab = this.add(row.group, row.url ? navigationUrl(row.url) : '');
        if (typeof row.id === 'string' && !this.rows.some(other => other !== tab && other.id === row.id)) tab.id = row.id;
        tab.title = String(row.title || '').slice(0, 180);
      } catch {}
    }
    for (const group of Object.keys(SITES)) {
      const match = this.rows.find(tab => tab.group === group && tab.id === saved?.lastActive?.[group]);
      this.lastActive[group] = match?.id || this.rows.find(tab => tab.group === group)?.id;
    }
    if (!this.rows.length) this.add('youtube', '');
    this.select(this.rows.find(tab => tab.id === saved?.activeId)?.id || this.rows[0].id);
  }
  get active() { return this.rows.find(tab => tab.id === this.activeId); }
  add(group, url = SITES[group]?.home) {
    if (!SITES[group]) throw new Error('请选择支持的站点分组');
    if (this.rows.length >= MAX_TABS) throw new Error('已打开 36 个标签，请先关闭暂时不用的页面');
    const tab = { id: randomUUID(), group, url: url ? navigationUrl(url) : '', title: '' };
    this.rows.push(tab); return tab;
  }
  select(id) {
    const tab = this.rows.find(row => row.id === id);
    if (!tab) throw new Error('该标签已关闭');
    this.activeId = tab.id; this.lastActive[tab.group] = tab.id; return tab;
  }
  switchGroup(group) {
    if (!SITES[group]) throw new Error('请选择支持的站点分组');
    return this.select(this.rows.find(tab => tab.id === this.lastActive[group])?.id ||
      this.rows.find(tab => tab.group === group)?.id || this.add(group).id);
  }
  find(url, group = siteForUrl(url) || this.active.group) {
    const key = contentKey(url);
    return this.rows.find(tab => tab.group === group && (key ? contentKey(tab.url) === key : tab.url === url));
  }
  remove(id) {
    const index = this.rows.findIndex(tab => tab.id === id);
    if (index < 0) return;
    const [tab] = this.rows.splice(index, 1), siblings = this.rows.filter(row => row.group === tab.group);
    const neighbor = this.rows.slice(0, index).reverse().find(row => row.group === tab.group) || siblings[0];
    if (this.lastActive[tab.group] === id) this.lastActive[tab.group] = neighbor?.id;
    if (this.activeId === id) this.select((neighbor || this.add(tab.group)).id);
    return tab;
  }
  serialize() {
    const tabs = this.rows.filter(tab => !isTaobaoLoginUrl(tab.url));
    const lastActive = Object.fromEntries(Object.keys(SITES).map(group => [group,
      tabs.find(tab => tab.group === group && tab.id === this.lastActive[group])?.id || tabs.find(tab => tab.group === group)?.id]));
    return { tabs: tabs.map(({ id, group, url, title }) => ({ id, group, url, title })),
      activeId: tabs.find(tab => tab.id === this.activeId)?.id || lastActive[this.active.group] || tabs[0]?.id,
      lastActive };
  }
}

// Run in the remote page without Node or an IPC bridge. Capture ordinary detail-link
// clicks before SPA routers consume them; the native window-open handler validates URLs.
const linkScript = `(() => {
  if (window.__collectorLinksInstalled) return;
  window.__collectorLinksInstalled = true;
  const siteForUrl = ${siteForUrl.toString()};
  const belongs = (host, domain) => host === domain || host.endsWith('.' + domain);
  const SITES = ${JSON.stringify(SITES)};
  const contentKey = ${contentKey.toString()};
  window.addEventListener('click', event => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const anchor = event.composedPath().find(node => node?.tagName === 'A' && node.href);
    if (!anchor || anchor.hasAttribute('download') || !contentKey(anchor.href) || contentKey(anchor.href) === contentKey(location.href)) return;
    // Xiaohongshu owns the click: its SPA opens a note overlay over the feed.
    if (siteForUrl(location.href) === 'xiaohongshu' && siteForUrl(anchor.href) === 'xiaohongshu') return;
    // A part selector within the same Bilibili video remains in its current tab.
    const current = contentKey(location.href), next = contentKey(anchor.href);
    if (current?.startsWith('bilibili:') && current.split(':')[1] === next.split(':')[1]) return;
    event.preventDefault(); event.stopImmediatePropagation(); window.open(anchor.href, '_blank');
  }, true);
  const pause = () => document.querySelectorAll('video,audio').forEach(media => media.pause());
  document.addEventListener('visibilitychange', () => { if (document.hidden) pause(); });
  document.addEventListener('play', event => { if (document.hidden) event.target.pause?.(); }, true);
})()`;
module.exports = { SiteTabs, linkScript, MAX_TABS };
