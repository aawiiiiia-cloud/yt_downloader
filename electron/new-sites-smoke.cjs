'use strict';
async function run(collector, check, until, wait) {
  const ui=code=>collector.window.webContents.executeJavaScript(code), source=collector.tabs.active;
  check('all five supported site groups appear in the UI', await ui("[...document.querySelectorAll('[data-site]')].map(button=>button.dataset.site).join(',')==='youtube,bilibili,xiaohongshu,vimeo,douyin'"));
  collector.openTab('https://www.douyin.com/video/1234567890123456789',{group:'douyin'});
  await until(()=>!collector.browser.webContents.isLoadingMainFrame()&&collector.state.url.includes('/video/1234567890123456789'),'Douyin fixture did not load');
  await collector.browser.webContents.mainFrame.executeJavaScript("(() => { const data=window.__INITIAL_STATE__; delete window.__INITIAL_STATE__; setTimeout(()=>window.__INITIAL_STATE__=data,700); })()");
  await collector.inspect();
  check('real Python parses matching Douyin data even when it arrives after DOM ready',collector.state.result?.site==='douyin'&&collector.state.result.items[0].formats[0].height===360);
  const douyin=collector.tabs.active;
  await until(()=>ui("document.querySelector('.cover-frame img')?.naturalWidth>0 && document.querySelector('.media-summary')?.textContent.includes('00:58')"),'Douyin cover or duration did not render');
  check('Douyin cover, precise duration and size render in the collection panel',await ui("document.querySelector('.media-summary').textContent.includes('0.34 MB')"));
  const invoke=collector.invoke.bind(collector);let requestedSite;
  collector.invoke=async(method,data)=>{if(method==='login'){requestedSite=data.site;return}return invoke(method,data)};
  await ui("document.getElementById('edge-login').click()");
  await until(()=>requestedSite==='douyin','Douyin Edge login was not routed');
  collector.invoke=invoke;
  check('new site login actions use their own site identifier',requestedSite==='douyin');
  await require('./douyin-open-smoke.cjs').run(collector,check,until,wait);
  await require('./manual-results-smoke.cjs').run(collector,check,until,wait);
  const receipt=collector.state.inspectionId;
  collector.openTab('https://vimeo.com/12345678',{group:'vimeo'});
  await until(()=>!collector.browser.webContents.isLoadingMainFrame()&&collector.state.url.includes('12345678'),'Vimeo fixture did not load');
  const runner=collector.withCookies;
  collector.withCookies=spec=>({cancel(){},promise:Promise.resolve({site:'vimeo',items:[{kind:'video',url:spec.url,title:'受控 Vimeo 视频',author:'测试作者',duration:62,thumbnail:'https://i.vimeocdn.com/video/cover.png',formats:[{id:'http-360p',video:true,audio:true,height:360,ext:'mp4',size:340000}]}]})});
  try{await collector.inspect()}finally{collector.withCookies=runner}
  await until(()=>ui("document.querySelector('.cover-frame img')?.naturalWidth>0 && document.querySelector('.media-summary')?.textContent.includes('01:02')"),'Vimeo cover or duration did not render');
  check('Vimeo fixture metadata reaches its own preview panel',collector.state.result?.site==='vimeo');
  const add=collector.queue.add;let saved;
  collector.queue.add=spec=>{saved=spec;return 'fixture-queue-id'};
  try{await collector.download({inspectionId:collector.state.inspectionId,itemIndex:0,formatId:'http-360p'})}finally{collector.queue.add=add}
  check('Vimeo queue snapshot keeps the selected muxed format without YouTube auth',saved.url==='https://vimeo.com/12345678'&&saved.formatId==='http-360p'&&saved.formatHasAudio);
  collector.switchSite('douyin');await wait(100);
  check('new site groups retain their independent parsed resources',collector.tabs.active===douyin&&collector.state.inspectionId===receipt);
  collector.activateTab(source.id);
}
module.exports={run};
