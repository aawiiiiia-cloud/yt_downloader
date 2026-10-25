'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { DownloadQueue } = require('./model.cjs');

async function run(collector, check, until, wait, profile) {
  const original = collector.queue, runner = collector.runTask, proxy = collector.effectiveProxy;
  const source = collector.tabs.active, settings = { ...collector.settings };
  const ui = code => collector.window.webContents.executeJavaScript(code);
  let queue; const jobs = [];
  const save = async (global,site) => {
    await ui(`document.getElementById('settings-tab').click();document.getElementById('global-concurrency').value=${global};document.getElementById('site-concurrency').value=${site};document.getElementById('global-concurrency').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('save-settings').click()`);
    await until(()=>collector.settings.globalConcurrency===global&&collector.settings.siteConcurrency===site&&
      collector.queue.limits.globalConcurrency===global&&collector.queue.limits.siteConcurrency===site,'saved concurrency did not reach the queue');
  };
  try {
    check('new profiles default to five global jobs and one job per site',settings.globalConcurrency===5&&settings.siteConcurrency===1);
    await ui("document.getElementById('settings-tab').click()");
    check('advanced concurrency settings start collapsed with only two shared limits',await ui("!document.getElementById('advanced-options').open && document.querySelectorAll('#advanced-options input').length===2 && document.getElementById('global-concurrency').value==='5' && document.getElementById('site-concurrency').value==='1'"));
    await ui("document.querySelector('#advanced-options summary').click()");await wait(60);
    const bounds=await ui("(()=>{const r=document.querySelector('aside').getBoundingClientRect();return{x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)}})()");
    fs.writeFileSync(path.join(profile,'..','advanced-concurrency-panel.png'),(await collector.window.webContents.capturePage(bounds)).toPNG());
    await ui("document.getElementById('global-concurrency').value=2;document.getElementById('site-concurrency').value=2;document.getElementById('global-concurrency').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('tasks-tab').click();document.getElementById('settings-tab').click()");
    check('unsaved concurrency choices survive panel switches',await ui("document.getElementById('global-concurrency').value==='2'&&document.getElementById('site-concurrency').value==='2'"));
    await save(2,2);
    check('saved shared concurrency limits persist to disk',JSON.parse(fs.readFileSync(path.join(profile,'settings.json'),'utf8')).siteConcurrency===2);
    await assert.rejects(()=>collector.invoke('settings',{...collector.settings,globalConcurrency:0}),/全局并发/);
    check('invalid concurrency is rejected before changing settings or scheduler limits',collector.settings.globalConcurrency===2&&collector.queue.limits.globalConcurrency===2);
    await save(2,1);

    queue = new DownloadQueue((spec,emit)=>collector.withCookies(spec,emit),()=>collector.publish(),collector.settings);
    collector.queue=queue;collector.effectiveProxy=async()=>'';
    collector.runTask=(request,emit)=>{
      const job={request,emit,cancelled:false};
      const handle={promise:new Promise((resolve,reject)=>Object.assign(job,{resolve,reject})),cancel(){job.cancelled=true;job.reject(Object.assign(new Error('操作已取消'),{cancelled:true}));}};
      jobs.push(job);return handle;
    };
    const bili=collector.tabs.rows.find(tab=>tab.group==='bilibili'&&tab.state.result?.items[0]?.kind==='video');
    assert.ok(bili,'existing local Bilibili fixture required');collector.activateTab(bili.id);
    const receipt=bili.state.inspectionId;
    const clickDownload=async()=>{
      await ui("document.getElementById('resources-tab').click();document.getElementById('download').click()");
      await until(()=>ui("!document.getElementById('tasks-panel').hidden"),'download click was not accepted');
      await until(()=>ui("!document.getElementById('download').disabled"),'download control stayed disabled after enqueue');
    };
    await clickDownload();await until(()=>jobs.length===1,'first download did not reach its worker');
    await clickDownload();await clickDownload();
    check('downloads beyond the single-site allowance remain clickable and enter the queue',queue.tasks.length===3&&queue.tasks.filter(t=>t.status==='queued').length===2&&jobs.length===1&&bili.state.inspectionId===receipt);
    const spec=(url)=>({command:'download',url,kind:'video',settings:{...collector.settings}});
    const vimeo=queue.add(spec('https://vimeo.com/246801'),'本地并发 Vimeo');
    await until(()=>jobs.length===2,'second website did not fill an available global slot');
    const firstCookie=jobs[0].request.cookieFile,secondCookie=jobs[1].request.cookieFile;
    check('parallel workers get independent site-scoped cookie files',firstCookie!==secondCookie&&fs.existsSync(firstCookie)&&fs.existsSync(secondCookie)&&
      fs.readFileSync(firstCookie,'utf8').includes('SESSDATA')&&!fs.readFileSync(secondCookie,'utf8').includes('SESSDATA'));
    const douyin=queue.add(spec('https://www.douyin.com/video/1234567890123456789'),'本地并发 抖音');
    check('downloads beyond the global allowance also remain queued',queue.tasks.find(t=>t.id===douyin).status==='queued'&&queue.active.size===2);
    jobs[0].emit('progress',{percent:31,status:'downloading'});
    check('parallel progress updates belong only to the matching task',queue.tasks.find(t=>t.spec===jobs[0].request)?.percent===31 ||
      queue.tasks.find(t=>t.status==='running'&&t.spec.url.includes('bilibili.com')).percent===31&&queue.tasks.find(t=>t.id===vimeo).percent===0);
    queue.cancel(vimeo);
    await until(()=>jobs.length===3&&queue.tasks.find(t=>t.id===vimeo).status==='cancelled','cancelled worker did not release its slot');
    check('cancelling one website lets another waiting website start without cancelling its neighbour',jobs[1].cancelled&&!jobs[0].cancelled&&queue.tasks.find(t=>t.id===douyin).status==='running'&&!fs.existsSync(secondCookie)&&fs.existsSync(firstCookie));
    jobs[0].resolve({folder:profile,count:1});
    await until(()=>jobs.length===4,'completion did not advance the oldest eligible job');
    check('completion releases its cookie file and starts the next queued site task',jobs[3].request.url.includes('bilibili.com')&&!fs.existsSync(firstCookie)&&queue.active.size===2);
    queue.close();
    await until(()=>queue.active.size===0,'concurrent shutdown did not settle every worker');
    check('shutdown cancels all remaining jobs and cleans every cookie snapshot',queue.tasks.every(t=>!['queued','running','cancelling'].includes(t.status))&&jobs.every(job=>!fs.existsSync(job.request.cookieFile)));
    collector.queue=original;collector.runTask=runner;collector.effectiveProxy=proxy;
    collector.activateTab(source.id);
    await save(4,2);
    check('non-default concurrency choices are saved for a separate-process restart',collector.settings.globalConcurrency===4&&collector.settings.siteConcurrency===2);
    await ui("document.getElementById('advanced-options').open=false;document.getElementById('resources-tab').click()");
  } finally {
    queue?.close();collector.queue=original;collector.runTask=runner;collector.effectiveProxy=proxy;
    if(!source.closed)collector.activateTab(source.id);
  }
}
module.exports={run};
