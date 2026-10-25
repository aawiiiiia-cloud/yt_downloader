'use strict';
const assert = require('node:assert/strict');
async function run(collector, check, until, wait) {
  const ui = code => collector.window.webContents.executeJavaScript(code), source = collector.tabs.active;
  collector.openTab('https://www.behance.net/gallery/123/Test?tracking_source=search', {group:'behance'});
  await until(()=>collector.state.site==='behance'&&!collector.browser.webContents.isLoadingMainFrame(),'Behance fixture did not load');
  const tab = collector.tabs.active;
  await collector.inspect();
  check('real gallery-dl parses the native browser project snapshot',collector.state.result?.site==='behance'&&collector.state.result.items[0].images.length===2);
  check('Behance source variants and original GIF are preserved',collector.state.result.items[0].images[0].url.includes('/source/')&&collector.state.result.items[0].images[1].url.endsWith('.gif'));
  await until(()=>ui("document.querySelector('[data-preview-image]')?.naturalWidth>0 && document.querySelector('[data-image-size]')?.textContent.includes('0.34 MB')"),'Behance preview or source size did not render');
  check('Behance previews and original source sizes reach the right panel',true);
  check('Behance avoids claiming Adobe login from anonymous cookies',await ui("document.getElementById('login-state').textContent.includes('公开作品') && document.getElementById('edge-login').hidden && !document.getElementById('web-login').hidden"));
  const add = collector.queue.add; let spec;
  collector.queue.add = value=>{spec=value;return 'controlled-behance-task'};
  try { await collector.download({inspectionId:collector.state.inspectionId,itemIndex:0,indices:[2]}); }
  finally { collector.queue.add=add; }
  check('Behance queued selection retains its frozen project and original indices',spec.url==='https://www.behance.net/gallery/123/Test' || spec.url.includes('/gallery/123/Test'));
  check('Behance task context remains private to the local queue',spec.indices.join(',')==='2'&&spec.context.projectId==='123'&&!JSON.stringify(collector.publicState()).includes('beconfig-store_state'));
  check('external videos are excluded from selectable resources and summarized once',collector.state.result.items.length===1&&collector.state.result.skippedExternal===1&&await ui("document.getElementById('skipped-external').textContent.includes('1') && !document.getElementById('open-source') && !document.getElementById('part-select')"));
  const receipt=collector.state.inspectionId;
  for (const url of ['https://www.youtube.com/watch?v=SmQd5BgLmd0','https://player.vimeo.com/video/123']) {
    await assert.rejects(()=>collector.withCookies({command:'download',url,referrer:spec.url}).promise,/默认不下载/);
    collector.state.result.items.push({kind:'video',url,title:'legacy external fixture',formats:[]});
    try { await assert.rejects(()=>collector.download({inspectionId:receipt,itemIndex:1}),/默认不下载/); }
    finally { collector.state.result.items.pop(); }
  }
  check('old cross-platform resource snapshots and queued downloads stay blocked',true);
  await ui("document.querySelector('[data-image=\"1\"]').checked=false");
  collector.switchSite('youtube');await wait(100);
  collector.activateTab(tab.id);await wait(120);
  check('skipping external players preserves the Behance receipt and image selection',collector.state.inspectionId===receipt&&await ui("!document.querySelector('[data-image=\"1\"]').checked && document.querySelector('[data-image=\"2\"]').checked"));
  await tab.view.webContents.mainFrame.executeJavaScript("(() => { const node=document.getElementById('beconfig-store_state');const data=JSON.parse(node.textContent);data.project.project.modules=data.project.project.modules.filter(module=>module.__typename==='EmbedModule');node.textContent=JSON.stringify(data); })()");
  await collector.inspect();
  await until(()=>ui("document.querySelector('#resources .empty')?.textContent.includes('已跳过 1')"),'external-only summary did not render');
  check('an external-only work displays a skip summary without a download action or parse error',!collector.state.error&&collector.state.result.items.length===0&&await ui("!document.getElementById('download')"));
  collector.activateTab(source.id);
}
module.exports={run};
