'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const post = structuredClone(require('./fixtures/instagram.json'));
post.carousel_media[1].video_duration = null;
post.carousel_media[1].video_dash_manifest = `<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" mediaPresentationDuration="PT12.5S">
<Period><AdaptationSet mimeType="video/mp4"><Representation id="v1" width="720" height="1280" bandwidth="800000" codecs="avc1.4d401f" FBContentLength="1250000">
<BaseURL>https://fixture.cdninstagram.com/dash-video.mp4</BaseURL></Representation></AdaptationSet>
<AdaptationSet mimeType="audio/mp4"><Representation id="a1" bandwidth="128000" codecs="mp4a.40.2" FBContentLength="200000">
<BaseURL>https://fixture.cdninstagram.com/dash-audio.mp4</BaseURL></Representation></AdaptationSet></Period></MPD>`;
const related = {...structuredClone(post), code: 'DfFPRM6n0u9', pk: '4018685395295226813',
  caption: {text: 'Related Instagram post'}};
let requests = 0;
function fixture(request) {
  const url = new URL(request.url);
  if (url.hostname !== 'www.instagram.com') return null;
  requests++;
  if (url.pathname === '/graphql/query') return new Response(JSON.stringify({ data: related }),
    { headers: { 'content-type': 'text/javascript' } });
  if (url.pathname.startsWith('/explore/search/') || url.searchParams.has('collector_search')) {
    return new Response(`<html><head><meta charset="utf-8"><title>Instagram search</title></head>
      <body style="height:3000px"><input aria-label="Search input" placeholder="Search" style="position:fixed;top:16px;left:20px">
      <div id="search-marker">Search results</div>
      <a id="search-post" target="_blank" href="/fixture/p/${post.code}/">Post</a>
      <a id="search-next" target="_blank" href="/fixture/p/${related.code}/">Next post</a>
      <a id="search-home" href="/">Home</a>
      <script type="application/json">${JSON.stringify({ data: post })}</script>
      <script>
        const openSearchPost = event => {
          event.preventDefault(); window.nativeSearchRouter = true;
          document.querySelector('input').hidden = true;
          history.pushState({}, '', event.currentTarget.href);
          const modal = document.createElement('div'); modal.id = 'native-post-modal'; modal.setAttribute('role', 'dialog');
          modal.innerHTML = '<button id="close-modal">Close</button><a id="modal-related" href="/fixture/p/${related.code}/">More posts</a>';
          document.body.append(modal);
          modal.querySelector('button').onclick = () => history.back();
          modal.querySelector('a').onclick = event => {
            event.preventDefault(); history.pushState({}, '', event.currentTarget.href);
            fetch('/graphql/query').then(response => response.json()).then(() => window.relatedResponseLoaded = true);
          };
          if (location.pathname.includes('${related.code}')) {
            fetch('/graphql/query').then(response => response.json()).then(() => window.relatedResponseLoaded = true);
          }
        };
        document.querySelector('#search-post').addEventListener('click', openSearchPost);
        document.querySelector('#search-next').addEventListener('click', openSearchPost);
        window.addEventListener('popstate', () => {
          if (!location.pathname.includes('/p/')) document.querySelector('#native-post-modal')?.remove();
        });
      </script></body></html>`, { headers: { 'content-type': 'text/html; charset=utf-8' } });
  }
  const body = url.pathname.startsWith('/accounts/login')
    ? '<form><input name="username"><input type="password"><button type="submit">Log in</button></form>'
    : `<h1>Instagram fixture</h1><script type="application/json">${JSON.stringify({
      data: url.pathname.includes(related.code) ? related : post,
    })}</script><a id="related-post" href="/fixture/p/${related.code}/">More posts</a>`;
  return new Response(`<html><head><meta charset="utf-8"><title>Instagram</title></head><body>${body}</body></html>`,
    {headers: {'content-type': 'text/html; charset=utf-8'}});
}
async function run(collector, check, until, wait, profile) {
  const previous = collector.tabs.activeId, before = new Set(collector.tabs.rows.map(tab => tab.id));
  const ui = code => collector.window.webContents.executeJavaScript(code);
  const url = 'https://www.instagram.com/p/Deg1QJmknzi/?img_index=3';
  const bounds = collector.window.getBounds();
  try {
    const tab = collector.openTab(url, {group: 'instagram'});
    await until(() => !tab.view.webContents.isLoadingMainFrame(), 'Instagram fixture did not load');
    const count = requests;
    await collector.inspect();
    const result = collector.state.result, receipt = collector.state.inspectionId;
    check('Instagram real worker reuses the loaded page without another metadata request', requests === count && result?.site === 'instagram');
    check('Instagram ignores img_index and retains sparse mixed-carousel image numbering',
      result.items[0].images.map(row => row.index).join(',') === '1,3');
    check('Instagram mixed carousel exposes video identity and real quality choices',
      result.items[1].mediaId === '102' && result.items[1].formats.some(row => row.height === 1280));
    check('Instagram MPD supplies duration and exact stream sizes without network probing',
      result.items[1].duration === 12.5 &&
      result.items[1].formats.find(row => row.id === 'dash-v1')?.size === 1250000 &&
      result.items[1].formats.find(row => row.id === 'dash-a1')?.size === 200000);
    await until(() => ui('document.querySelectorAll("[data-image]").length === 2 && [...document.querySelectorAll("[data-preview-image]")].every(image => image.naturalWidth > 0)'), 'Instagram previews did not render');
    check('Instagram shows official login and import controls without Edge',
      await ui('!document.querySelector("#web-login").hidden && document.querySelector("#edge-login").hidden && !document.querySelector("#import-login").hidden'));
    await until(() => ui('[...document.querySelectorAll("[data-image-dimensions]")].some(row => row.textContent.includes("4×3"))'), 'Instagram unknown dimensions were not probed');
    const add = collector.queue.add;
    let spec;
    collector.queue.add = value => { spec = value; return 'instagram-fixture-task'; };
    try {
      await collector.download({inspectionId: receipt, itemIndex: 0, indices: [3]});
      check('Instagram queues the original image index with its frozen post', spec.indices.join(',') === '3' && spec.context.instagram.post.code === post.code);
      await collector.download({inspectionId: receipt, itemIndex: 1, formatId: result.items[1].formats.find(row => row.height === 1280).id});
      check('Instagram queues video identity and selected format', spec.mediaId === '102' && !!spec.formatId);
    } finally { collector.queue.add = add; }
    const context = await collector.refreshInstagramContext({url, context: {instagram: {post}}});
    check('Instagram expired-media refresh preserves every carousel child', context.instagram.post.carousel_media.length === 3);
    const changed = structuredClone(post); changed.carousel_media[2].pk = '999';
    await assert.rejects(() => collector.refreshInstagramContext({url, context: {instagram: {post: changed}}}), /列表已变化/);
    check('Instagram refresh rejects changed media identities', true);
    for (const [width, height] of [[1440,920], [1060,700]]) {
      collector.window.setSize(width, height);
      await wait(300);
      check(`Instagram active site remains visible at ${width}px`, await ui(`(() => {
        const strip = document.querySelector(".site-links").getBoundingClientRect();
        const active = document.querySelector("[data-site=instagram]").getBoundingClientRect();
        return active.left >= strip.left - 1 && active.right <= strip.right + 1;
      })()`));
      check(`Instagram panel controls fit at ${width}px`, await ui(`(() => {
        const panel = document.querySelector("aside").getBoundingClientRect();
        return [...document.querySelectorAll(".login-tools button:not([hidden]), .image-download, .image-meta")]
          .every(node => { const rect = node.getBoundingClientRect(); return rect.left >= panel.left && rect.right <= panel.right && node.scrollWidth <= node.clientWidth + 1; });
      })()`));
      const image = await collector.window.webContents.capturePage();
      check(`Instagram screenshot is nonblank at ${width}px`, !image.isEmpty() && image.getBitmap().some(byte => byte !== 255 && byte !== 0));
      fs.writeFileSync(path.join(profile, '..', `instagram-${width}.png`), image.toPNG());
    }
    await ui('document.querySelector("#part-select").value="1";document.querySelector("#part-select").dispatchEvent(new Event("change",{bubbles:true}))');
    await until(() => ui('document.querySelectorAll("#format-select option").length > 1'), 'Instagram quality menu did not render');
    check('Instagram video quality control is available in the real UI', true);
    check('Instagram video UI displays manifest duration and combined estimated size',
      await ui('document.querySelector(".media-summary").textContent.includes("00:12") && document.querySelector(".media-summary").textContent.includes("1.45 MB")'));
    const tabsBeforeRelated = collector.tabs.rows.length, sourceWeb = tab.view.webContents;
    await sourceWeb.mainFrame.executeJavaScript('document.querySelector("#related-post").click()', true);
    await until(() => collector.browser.webContents.getURL().includes(related.code) &&
      !collector.browser.webContents.isLoadingMainFrame(), 'Related Instagram post did not open');
    const relatedTab = collector.tabs.active;
    check('Instagram more-posts stays in its original tab and native view',
      relatedTab === tab && tab.view.webContents === sourceWeb && collector.tabs.rows.length === tabsBeforeRelated);
    const relatedRequests = requests;
    await collector.inspect(relatedTab);
    check('Instagram author-prefixed more-posts URL parses the new post without another fetch',
      relatedTab.state.result?.url === `https://www.instagram.com/p/${related.code}/` &&
      relatedTab.state.result?.items[0].id === related.code && requests === relatedRequests);
    check('Instagram nested-route cache is bound to the new current post',
      collector.resultMatchesPage(relatedTab) && relatedTab.state.inspectionId !== receipt);
    await assert.rejects(() => collector.download({inspectionId: receipt, itemIndex: 0, indices: [3]}), /重新解析/);
    check('Instagram previous-post receipts are rejected after parsing the related post', true);
    const addRelated = collector.queue.add;
    let relatedSpec;
    collector.queue.add = value => { relatedSpec = value; return 'related-instagram-task'; };
    try {
      await collector.download({inspectionId: relatedTab.state.inspectionId, itemIndex: 0, indices: [3]});
      check('Instagram related-post download freezes the new post instead of the previous one',
        relatedSpec.context.instagram.post.code === related.code &&
        relatedSpec.url === `https://www.instagram.com/p/${related.code}/`);
    } finally { collector.queue.add = addRelated; }
    sourceWeb.navigationHistory.goBack();
    await until(() => sourceWeb.getURL() === url && !sourceWeb.isLoadingMainFrame(), 'Related post back navigation did not restore the source');
    await collector.inspect(tab);
    check('Instagram related-post back navigation restores the original post identity',
      collector.tabs.active === tab && tab.state.result?.items[0].id === post.code && collector.resultMatchesPage(tab));

    for (const searchUrl of [
      'https://www.instagram.com/explore/search/keyword/?q=fixture',
      'https://www.instagram.com/?collector_search=1',
    ]) {
      const searchTab = collector.openTab(searchUrl, { group: 'instagram' }), searchWeb = searchTab.view.webContents;
      const searchTabs = collector.tabs.rows.length;
      await until(() => !searchWeb.isLoadingMainFrame() &&
        searchWeb.executeJavaScript('!!window.__collectorLinksInstalled'), 'Search fixture did not load');
      await searchWeb.executeJavaScript('window.searchMarker="retained";window.scrollTo(0,700)');
      const scroll = await searchWeb.executeJavaScript('window.scrollY');
      await searchWeb.executeJavaScript('document.querySelector("#search-post").click()', true);
      await until(() => searchTab.url.includes(post.code), 'Instagram native search modal did not open');
      check('Instagram search click reaches the native modal without adding or replacing a tab: ' + searchUrl,
        collector.tabs.active === searchTab && collector.tabs.rows.length === searchTabs &&
        searchTab.view.webContents === searchWeb && await searchWeb.executeJavaScript(
          'window.nativeSearchRouter && window.searchMarker === "retained" && !!document.querySelector("#native-post-modal")'));
      const searchRequests = requests;
      await collector.inspect(searchTab);
      check('Instagram native search modal parses its current post from the loaded document',
        searchTab.state.result?.items[0].id === post.code && collector.resultMatchesPage(searchTab) && requests === searchRequests);
      await searchWeb.executeJavaScript('document.querySelector("#modal-related").click()', true);
      await until(() => searchWeb.executeJavaScript('!!window.relatedResponseLoaded'), 'Related modal API response did not arrive');
      const modalRequests = requests;
      await collector.inspect(searchTab);
      check('Instagram native modal switches posts using the browser response without a second metadata request',
        searchTab.state.result?.items[0].id === related.code && collector.resultMatchesPage(searchTab) && requests === modalRequests);
      await searchWeb.executeJavaScript('history.back()', true);
      await until(() => searchTab.url.includes(post.code), 'Modal back navigation did not restore its first post');
      await searchWeb.executeJavaScript('document.querySelector("#close-modal").click()', true);
      await until(() => searchTab.url === searchUrl, 'Closing the modal did not restore search');
      check('Instagram closing the native modal retains search results and scroll',
        await searchWeb.executeJavaScript(`window.searchMarker === "retained" && !document.querySelector("#native-post-modal") && window.scrollY === ${scroll}`));
      await assert.rejects(() => collector.inspect(searchTab), /详情/);
      check('Instagram closed search modal cannot parse a background recommendation', true);
      const addSearch = collector.queue.add;
      const searchSpecs = [];
      collector.queue.add = value => { searchSpecs.push(value); return 'instagram-search-task-' + searchSpecs.length; };
      try {
        for (const [itemIndex, selector, code] of [
          [0, '#search-next', related.code], [1, '#search-post', post.code], [0, '#search-next', related.code],
        ]) {
          await searchWeb.executeJavaScript(`document.querySelector('${selector}').click()`, true);
          await until(() => searchTab.url.includes(code) &&
            searchWeb.executeJavaScript('!!document.querySelector("#native-post-modal")'),
            'Instagram search did not reopen its native modal after parsing');
          await collector.inspect(searchTab);
          check('Instagram repeated search modal parses the matching post without replacing its view',
            searchTab.state.result?.items[0].id === code && collector.resultMatchesPage(searchTab) &&
            collector.tabs.active === searchTab && collector.tabs.rows.length === searchTabs);
          await until(() => ui(`document.querySelector('#part-select')?.options.length === 2`),
            'Repeated search post resources did not render');
          await ui(`document.querySelector('#part-select').value='${itemIndex}';
            document.querySelector('#part-select').dispatchEvent(new Event('change',{bubbles:true}))`);
          const button = itemIndex === 0 ? '[data-download-image="3"]' : '#download';
          await until(() => ui(`!!document.querySelector('${button}') && !document.querySelector('${button}').disabled`),
            'Repeated search download button did not become available');
          if (itemIndex === 1) {
            await ui(`document.querySelector('#format-select').selectedIndex=1;
              document.querySelector('#format-select').dispatchEvent(new Event('change',{bubbles:true}))`);
          }
          const queued = searchSpecs.length;
          await ui(`document.querySelector('${button}').click()`);
          await until(() => searchSpecs.length === queued + 1, 'Search post UI did not queue the download');
          const spec = searchSpecs.at(-1);
          check('Instagram search UI queues the correct ' + (itemIndex === 0 ? 'image' : 'video') + ' before returning',
            spec.context.instagram.post.code === code &&
            (itemIndex === 0 ? spec.indices.join(',') === '3' : spec.mediaId === '102' && !!spec.formatId));
          await searchWeb.executeJavaScript('document.querySelector("#close-modal").click()', true);
          await until(() => searchTab.url === searchUrl, 'Downloaded post did not return to search');
          check('Instagram download and modal close retain hidden-input search, scroll and the original tab',
            collector.tabs.active === searchTab && collector.tabs.rows.length === searchTabs &&
            await searchWeb.executeJavaScript(`window.searchMarker === 'retained' &&
              document.querySelector('input').hidden && !document.querySelector('#native-post-modal') &&
              window.scrollY === ${scroll}`));
        }
      } finally { collector.queue.add = addSearch; }
      await searchWeb.executeJavaScript(`(() => {
        const link = document.querySelector('#search-post'); link.replaceWith(link.cloneNode(true));
        document.querySelector('#search-post').click();
      })()`, true);
      await until(() => searchTab.url.includes(post.code) && !searchWeb.isLoadingMainFrame(),
        'Instagram plain search link did not use its original view');
      check('Instagram search without an SPA router falls back to document navigation in the same tab',
        collector.tabs.active === searchTab && collector.tabs.rows.length === searchTabs && searchTab.view.webContents === searchWeb);
      collector.activateTab(tab.id); collector.closeTab(searchTab.id);
    }
    const home = collector.openTab('https://www.instagram.com/', { group: 'instagram' });
    await until(() => !home.view.webContents.isLoadingMainFrame() &&
      home.view.webContents.executeJavaScript('!!window.__collectorLinksInstalled'), 'Instagram home fixture did not load');
    const homeTabs = collector.tabs.rows.length;
    await home.view.webContents.executeJavaScript('document.querySelector("#related-post").click()', true);
    await until(() => collector.tabs.active !== home && !collector.browser.webContents.isLoadingMainFrame(),
      'Instagram homepage post did not open a separate tab');
    const homePost = collector.tabs.active;
    check('Instagram homepage post links still open a separate tab and retain their homepage',
      collector.tabs.rows.length === homeTabs + 1 && home.view.webContents.getURL() === 'https://www.instagram.com/');
    collector.activateTab(tab.id); collector.closeTab(homePost.id); collector.closeTab(home.id);
    await collector.openWebLogin('instagram');
    await until(() => collector.browser.webContents.getURL().includes('/accounts/login'), 'Instagram official login did not open');
    check('Instagram login preserves the source post in its own tab', tab.view.webContents.getURL() === url);
    await collector.activateTab(tab.id);
    await tab.view.webContents.mainFrame.executeJavaScript('document.querySelectorAll("script").forEach(node=>node.remove())');
    const start = requests;
    await collector.inspect();
    check('Instagram missing page data uses one browser-network supplement', collector.state.result?.site === 'instagram' && requests === start + 1);
  } finally {
    collector.window.setBounds(bounds);
    collector.activateTab(previous);
    for (const tab of [...collector.tabs.rows]) if (!before.has(tab.id)) collector.closeTab(tab.id);
  }
}
module.exports = {fixture, run};
