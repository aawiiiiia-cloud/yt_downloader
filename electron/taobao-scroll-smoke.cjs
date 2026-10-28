'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { desktopCapturer } = require('electron');
const { HOME_MIN_WIDTH } = require('./taobao-scroll.cjs');

function fixture(request) {
  const url = new URL(request.url);
  if (url.hostname !== 'www.taobao.com' || url.pathname !== '/' || !url.searchParams.has('collector-scroll-fixture')) return null;
  // Reproduce the clipping shown in the report: a desktop-width home grid,
  // important overflow hiding, a hidden native scrollbar and a long page.
  return new Response(`<!doctype html><html style="overflow-x:hidden!important;scrollbar-width:none!important"><head><meta charset="utf-8"><title>Controlled home scrolling</title><style>
    html,body{margin:0;overflow-x:hidden!important;scrollbar-width:none!important}
    ::-webkit-scrollbar{display:none!important}
    body{font:16px Arial,sans-serif;color:#333;background:#fff}
    main{box-sizing:border-box;min-width:${HOME_MIN_WIDTH}px;padding:24px}
    header{height:80px;display:flex;align-items:center;gap:40px;color:#f50;font-size:34px}
    .search{font-size:18px;border:2px solid #f50;border-radius:12px;padding:15px;width:560px}
    .grid{display:grid;grid-template-columns:repeat(5,1fr);gap:20px}
    article{height:245px;border:1px solid #eee;border-radius:12px;overflow:hidden}
    .picture{height:170px;background:linear-gradient(135deg,#dfe8ec,#a4bdcb);display:grid;place-items:center;color:#435b6b;font-size:30px}
    article:nth-child(5n) .picture{background:#ffd5bf;color:#b9450b}
    .name{padding:12px;font-size:18px}.price{color:#f50;padding:0 12px}
  </style></head><body style="overflow-x:hidden!important"><main data-scroll-fixture><header><b>\u6dd8\u5b9d</b><span class="search">\u53d7\u63a7\u6d4b\u8bd5\uff1a\u7a84\u7a97\u53e3\u6a2a\u5411\u6eda\u52a8</span></header>
  <div class="grid">${Array.from({ length: 25 }, (_, i) => `<article><div class="picture">${String(i + 1).padStart(2, '0')}</div><div class="name">${i % 5 === 4 ? '\u53f3\u4fa7\u5546\u54c1\uff08\u539f\u6765\u88ab\u622a\u65ad\uff09' : '\u53d7\u63a7\u5546\u54c1'}</div><div class="price">\u00a5${99 + i}</div></article>`).join('')}</div></main></body></html>`, { headers: { 'content-type': 'text/html; charset=utf-8' } });
}
const metricsScript = `(() => {
  const root = document.documentElement, body = document.body;
  const last = document.querySelector('article:nth-child(5)')?.getBoundingClientRect();
  return { width: innerWidth, height: innerHeight, clientWidth: root.clientWidth, clientHeight: root.clientHeight,
    scrollWidth: root.scrollWidth, scrollHeight: root.scrollHeight, x: scrollX, y: scrollY,
    horizontalBar: innerHeight - root.clientHeight, verticalBar: innerWidth - root.clientWidth,
    overflowX: getComputedStyle(root).overflowX, minWidth: getComputedStyle(body).minWidth,
    rightItemVisible: !!last && last.right <= root.clientWidth && last.left >= 0 };
})()`;

async function run(collector, check, until, wait) {
  const source = collector.tabs.active, size = collector.window.getSize(), width = collector.settings.panelWidth;
  const ui = code => collector.window.webContents.executeJavaScript(code);
  const directory = path.join(__dirname, '..', 'electron-artifacts', 'taobao-horizontal-2026-10-28');
  fs.mkdirSync(directory, { recursive: true });
  let tab;
  const report = { liveSite: false, fixture: 'desktop home with hidden root overflow and scrollbar', minWidth: HOME_MIN_WIDTH };
  const capture = async name => {
    // Only capture this exact isolated window, never another user window.
    // Screenshots are evidence, not a prerequisite in a headless session.
    try {
      const [width, height] = collector.window.getSize();
      const sources = await desktopCapturer.getSources({ types: ['window'], thumbnailSize: { width, height } });
      const id = collector.window.getMediaSourceId();
      const own = sources.find(source => source.id === id || source.id.split(':').slice(0, 2).join(':') === id.split(':').slice(0, 2).join(':'));
      if (!own || own.thumbnail.isEmpty()) throw new Error('Test window capture unavailable in this desktop session');
      fs.writeFileSync(path.join(directory, name), own.thumbnail.toPNG());
    } catch (error) {
      (report.captureWarnings ||= []).push({ name, message: error.message });
    }
  };
  try {
    tab = collector.openTab('https://www.taobao.com/?collector-scroll-fixture=1', { group: 'taobao' });
    const web = tab.view.webContents, measure = () => web.mainFrame.executeJavaScript(metricsScript);
    await until(() => web.mainFrame.executeJavaScript('!!document.querySelector("[data-scroll-fixture]")'), 'home scroll fixture did not load');
    await tab.homeScroll.pending;
    collector.window.setSize(1060, 760); await wait(250);
    await until(async () => (await measure()).width < HOME_MIN_WIDTH, 'browser did not resize to narrow mode');
    const slotScript = '(()=>{const r=document.getElementById("browser-slot").getBoundingClientRect();return {x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)}})()';
    await until(async () => {
      const slot = await ui(slotScript), bounds = tab.view.getBounds();
      return Object.keys(slot).every(key => slot[key] === bounds[key]);
    }, 'native browser bounds did not settle after resizing');
    report.slot = await ui(slotScript); report.viewBounds = tab.view.getBounds();
    check('Taobao narrow native view remains exactly inside its browser slot', Object.keys(report.slot).every(key => report.slot[key] === report.viewBounds[key]));
    // Temporarily deactivate this compatibility sheet to capture the actual
    // pre-fix behavior in the same Chromium view, then enable it again.
    await tab.homeScroll.pending; await tab.homeScroll.setEnabled(false); await wait(60);
    report.before = await measure();
    check('controlled narrow home reproduces missing horizontal scrollbar and clipped right column', report.before.overflowX === 'hidden' && report.before.horizontalBar === 0 && !report.before.rightItemVisible);
    await capture('home-before.png');
    await tab.homeScroll.requestSync(); await wait(80);
    report.after = await measure();
    check('narrow Taobao home has a native horizontal scrollbar at the viewport bottom', report.after.horizontalBar > 0 && report.after.scrollWidth > report.after.clientWidth && report.after.overflowX === 'auto');
    check('home page keeps vertical scrolling and its desktop grid without zooming', report.after.verticalBar > 0 && report.after.scrollHeight > report.after.clientHeight && web.getZoomFactor() === 1);
    await capture('home-after-left.png');
    // Drag the real Chromium thumb with native input, not a scripted scroll.
    collector.window.focus(); web.focus(); await wait(100);
    report.windowFocused = collector.window.isFocused();
    const m = report.after, x = Math.round(m.clientWidth * m.clientWidth / m.scrollWidth / 2), y = Math.round(m.height - m.horizontalBar / 2);
    web.sendInputEvent({ type: 'mouseMove', x, y });
    web.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount: 1 }); await wait(30);
    web.sendInputEvent({ type: 'mouseMove', x: m.clientWidth - 20, y, modifiers: ['leftButtonDown'] }); await wait(30);
    web.sendInputEvent({ type: 'mouseUp', x: m.clientWidth - 20, y, button: 'left', clickCount: 1 });
    const dragDeadline = Date.now() + 2000;
    while (Date.now() < dragDeadline && (await measure()).x === 0) await wait(80);
    report.nativeDrag = (await measure()).x > 0;
    if (!report.nativeDrag && report.captureWarnings?.length) {
      // A noninteractive desktop session cannot exercise Chromium's native
      // scrollbar hit testing. Keep that limitation explicit, but still test
      // actual root scrollability and all resize/navigation behavior.
      report.nativeInputWarning = 'Native dragging could not be verified in this desktop session; programmatic scrolling tested instead';
      await web.mainFrame.executeJavaScript('scrollTo(document.documentElement.scrollWidth,0)'); await wait(80);
    }
    report.dragged = await measure();
    check(report.nativeDrag ? 'dragging the native scrollbar exposes the complete right-hand column' : 'programmatic horizontal scrolling exposes the complete right-hand column (native drag unverified)', report.dragged.x > 0 && report.dragged.rightItemVisible);
    await capture('home-after-right.png');
    await web.mainFrame.executeJavaScript('scrollTo(scrollX,500)'); await wait(80);
    report.scrolledDown = await measure();
    check('horizontal scrollbar remains at viewport bottom while scrolling vertically', report.scrolledDown.y >= 400 && report.scrolledDown.horizontalBar > 0);
    collector.activateTab(source.id); await wait(60); collector.activateTab(tab.id); await wait(80);
    check('switching tabs keeps the home page horizontal and vertical positions', (await measure()).x === report.scrolledDown.x && (await measure()).y === report.scrolledDown.y);
    collector.window.setContentSize(1800, 900); await wait(250);
    const resizeDeadline = Date.now() + 2000;
    while (Date.now() < resizeDeadline && (await measure()).clientWidth < HOME_MIN_WIDTH) await wait(80);
    report.wide = await measure();
    if (report.wide.clientWidth >= HOME_MIN_WIDTH) {
      check('wide Taobao home has no redundant horizontal scrollbar', report.wide.horizontalBar === 0 && report.wide.x === 0);
    } else if (report.captureWarnings?.length) {
      report.wideResizeWarning = 'Native viewport resize could not be verified in this noninteractive desktop session';
    } else {
      throw new Error('Browser did not resize to wide mode');
    }
    collector.window.setSize(1060, 760);
    await until(async () => (await measure()).horizontalBar > 0, 'home scrollbar did not return after shrinking');
    if (!report.wideResizeWarning) check('shrinking the window restores the home horizontal scrollbar', (await measure()).horizontalBar > 0);
    const loaded = new Promise(resolve => web.once('did-finish-load', resolve)); web.reload(); await loaded; await tab.homeScroll.pending; await wait(80);
    check('reloading Taobao home reinstalls the native scrollbar automatically', (await measure()).horizontalBar > 0 && !!tab.homeScroll.key);
    await web.mainFrame.executeJavaScript("history.pushState({}, '', '/compatibility-not-home')"); await wait(100); await tab.homeScroll.pending;
    check('leaving the home SPA route removes compatibility rules', !tab.homeScroll.enabled && (await measure()).overflowX === 'hidden');
    await web.mainFrame.executeJavaScript("history.pushState({}, '', '/?collector-scroll-fixture=1')"); await wait(100); await tab.homeScroll.pending;
    check('returning to the home SPA route restores compatibility rules', tab.homeScroll.enabled && (await measure()).horizontalBar > 0);
    const aside = await ui('(()=>{const r=document.getElementById("collection-pane").getBoundingClientRect();return {x:r.x,width:r.width}})()');
    check('home scrolling does not cover or change the collection panel', tab.view.getBounds().x + tab.view.getBounds().width <= aside.x && aside.width >= 330);
    report.ok = true;
  } catch (error) { report.ok = false; report.error = error.stack; throw error; }
  finally {
    fs.writeFileSync(path.join(directory, 'scroll-result.json'), JSON.stringify(report, null, 2));
    collector.settings.panelWidth = width; collector.window.setSize(...size);
    if (!source.closed) collector.activateTab(source.id);
    if (tab && !tab.closed) collector.closeTab(tab.id);
    await wait(150);
  }
}
module.exports = { fixture, run, metricsScript };
