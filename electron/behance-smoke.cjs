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
  await ui("document.querySelector('[data-image=\"1\"]').checked=false; document.getElementById('part-select').value='1'; document.getElementById('part-select').dispatchEvent(new Event('change',{bubbles:true}))");
  await until(()=>ui("!!document.getElementById('open-source')"),'external video action did not render');
  check('embedded YouTube offers its own site action instead of an anonymous download',await ui("!document.getElementById('download') && document.getElementById('open-source').textContent.includes('YouTube')"));
  const receipt=collector.state.inspectionId;
  await assert.rejects(()=>collector.download({inspectionId:receipt,itemIndex:1}),/来源站点/);
  await assert.rejects(()=>collector.invoke('open-source',{inspectionId:'stale',itemIndex:1}),/已变化/);
  check('stale or unparsed external videos cannot enter the download queue',true);
  await ui("document.getElementById('open-source').click()");
  await until(()=>collector.tabs.active.group==='youtube'&&collector.state.url.includes('SmQd5BgLmd0'),'embedded YouTube did not open in its group');
  const youtube=collector.tabs.active;
  check('opening an embedded video retains the Behance page and parsed resources',tab.state.inspectionId===receipt&&tab.view.webContents.getURL().includes('/gallery/123/'));
  collector.activateTab(tab.id);await wait(120);
  check('returning to Behance restores the selected resource entry',await ui("document.getElementById('part-select').value==='1'"));
  collector.closeTab(youtube.id);collector.activateTab(source.id);
}
module.exports={run};
