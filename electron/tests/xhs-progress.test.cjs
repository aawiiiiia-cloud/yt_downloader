'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { waitForPreparation } = require('../task-preflight.cjs');
const { DownloadQueue } = require('../model.cjs');
const { taskProgress, taskSpeed } = require('../ui/task-progress.js');
const tick = () => new Promise(resolve => setImmediate(resolve));
test('unknown-length and connecting phases are indeterminate, not zero', () => {
  assert.deepEqual(taskProgress({status:'running',detail:'image-connecting',percent:null}),
    {stage:'正在连接图片',text:'等待数据',attribute:''});
  assert.equal(taskProgress({status:'running',detail:'image-receiving',percent:null,downloadedBytes:65536}).text,'已接收 64.0 KB');
  assert.equal(taskProgress({status:'running',detail:'image-receiving',percent:37.2}).text,'37%');
  assert.equal(taskProgress({status:'running',detail:'image-validating',percent:99.5}).text,'99%');
  assert.equal(taskProgress({status:'completed',percent:100}).text,'100%');
});
test('queue publishes preparation log before starting worker, retains null and clamps progress', async () => {
  let callback, resolve;
  const queue = new DownloadQueue((_spec, emit) => {
    assert.ok(queue.tasks[0].logs.length); assert.equal(queue.tasks[0].percent,null);
    callback = emit; return {promise:new Promise(r=>resolve=r),cancel(){}};
  },()=>{});
  queue.add({url:'https://www.xiaohongshu.com/explore/abc123'},'fixture');
  callback('progress',{status:'image-receiving',percent:null,downloadedBytes:1024});
  assert.equal(queue.tasks[0].percent,null); assert.equal(queue.tasks[0].downloadedBytes,1024);
  callback('progress',{status:'image-receiving',percent:700}); assert.equal(queue.tasks[0].percent,100);
  callback('progress',{status:'image-validating',percent:99}); assert.equal(queue.tasks[0].downloadedBytes,0);
  resolve({}); await tick(); assert.equal(queue.tasks[0].percent,100);
});
test('preparation passes results, surfaces operation errors and times out hung proxy resolution', async () => {
  assert.equal(await waitForPreparation(()=>Promise.resolve('proxy'),{timeoutMs:50}),'proxy');
  await assert.rejects(waitForPreparation(()=>{throw new Error('test');},{timeoutMs:50}),/test/);
  await assert.rejects(waitForPreparation(()=>new Promise(()=>{}),{timeoutMs:15,label:'系统代理解析'}),/系统代理解析等待超时/);
});
test('preparation cancellation settles promptly without waiting for Chromium', async () => {
  const controller = new AbortController();
  const pending = waitForPreparation(()=>new Promise(()=>{}),{signal:controller.signal,timeoutMs:1000});
  controller.abort(); await assert.rejects(pending,error=>error.cancelled===true);
  let called = false;
  await assert.rejects(waitForPreparation(()=>{called=true;},{signal:controller.signal}),error=>error.cancelled===true);
  assert.equal(called,false);
});
test('late preparation rejections are observed after timeout and cancellation', async () => {
  let reject; const pending = waitForPreparation(()=>new Promise((_r,j)=>reject=j),{timeoutMs:15});
  await assert.rejects(pending,/超时/); reject(new Error('late failure')); await tick();
});
// Load the actual main-process class without starting Electron or touching user data.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const mainPath = path.resolve(__dirname,'../main.cjs'), localRequire = createRequire(mainPath);
const fakeApp = {setName(){},setPath(){},on(){},requestSingleInstanceLock(){return true;},quit(){},whenReady(){return new Promise(()=>{});}};
const moduleObject = {exports:{}};
vm.runInNewContext(fs.readFileSync(mainPath,'utf8'),{
  require:name=>name==='electron'?{app:fakeApp}:localRequire(name),
  module:moduleObject,__dirname:path.dirname(mainPath),process,console,URL,
  AbortController,setTimeout,clearTimeout,setInterval,clearInterval,Buffer,
},{filename:mainPath});
const { CollectorApp } = moduleObject.exports;
function fixture(overrides={}) {
  return Object.assign(Object.create(CollectorApp.prototype),{
    externalTasks:new Set(),track:task=>task,userAgent:'FixtureAgent',
    cookieSnapshot:async()=>path.join(__dirname,'nonexistent-fixture.cookies'),
    effectiveProxy:async()=>'',runTask:()=>({promise:Promise.resolve({count:1}),cancel(){}}),
    credentials:{remove:file=>fs.rmSync(file,{force:true})},
  },overrides);
}
const spec = {command:'download',url:'https://www.xiaohongshu.com/explore/abc123?xsec_token=secret',settings:{}};
test('actual preflight emits each stage before launching Python', async () => {
  let allowCookie, allowProxy, launched=false; const logs=[];
  const app=fixture({cookieSnapshot:()=>new Promise(r=>allowCookie=r),effectiveProxy:()=>new Promise(r=>allowProxy=r),
    runTask:request=>{launched=true;assert.equal(request.userAgent,'FixtureAgent');assert.equal(request.settings.proxy,'http://fixture');return {promise:Promise.resolve({}),cancel(){}};}});
  const task=app.withCookies(spec,(kind,data)=>logs.push(data));
  await tick();assert.match(logs[0],/登录资料/);assert.equal(launched,false);
  allowCookie(path.join(__dirname,'nonexistent-fixture.cookies'));await tick();assert.match(logs[1],/代理/);
  allowProxy('http://fixture');await task.promise;assert.equal(launched,true);assert.match(logs[2],/启动下载器/);
  assert.ok(!logs.join('\n').includes('secret'));
});
test('actual proxy-wait cancellation frees queue slot promptly and cleans snapshot', async () => {
  const os=require('node:os'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'xhs-preflight-'));
  const cookie=path.join(temp,'fixture.cookies');fs.writeFileSync(cookie,'fake');
  let launched=false;
  const app=fixture({cookieSnapshot:async()=>cookie,effectiveProxy:()=>new Promise(()=>{}),
    runTask:()=>{launched=true;throw new Error('must not launch');}});
  try {
    const task=app.withCookies(spec,()=>{});await tick();task.cancel();
    await assert.rejects(task.promise,error=>error.cancelled===true);
    assert.equal(launched,false);assert.equal(fs.existsSync(cookie),false);
  } finally {fs.rmSync(temp,{recursive:true,force:true});}
});
test('actual cookie snapshot does not write a late file after cancellation', async () => {
  let finish; const app=fixture({browserSession:{cookies:{get:()=>new Promise(r=>finish=r)}}});
  // Use the real cookieSnapshot; writeFileSync is unreachable because cancellation is checked first.
  app.cookieSnapshot=CollectorApp.prototype.cookieSnapshot;
  const task=app.withCookies(spec,()=>{});await tick();task.cancel();
  await assert.rejects(task.promise,error=>error.cancelled===true);
  finish([]);await tick();
});

const noteContext={noteId:'6a8fbc2400000000070063fc',note:{imageList:[{fileId:'resource'}]}};
function inspectionFixture(readPage) {
  const tab={state:{},sequence:0,closed:false,assetsAbort:new AbortController(),
    view:{webContents:{getURL:()=>`https://www.xiaohongshu.com/explore/${noteContext.noteId}`}}};
  const requests=[],logs=[],result={site:'xiaohongshu',items:[]};
  const app=fixture({tabs:{active:tab},cache:new Map(),closing:false,
    invalidate(){},publish(){},log:text=>logs.push(text),readPage,
    withCookies:request=>{requests.push(request);return {promise:Promise.resolve(result),cancel(){}};},
  });
  return {app,tab,requests,logs,result};
}
test('XHS ready-page parsing reads once and passes the existing note to Python',async()=>{
  let reads=0;
  const {app,tab,requests,result}=inspectionFixture(async(_script,_tab,_wait,signal)=>{
    reads++;assert.equal(signal,tab.assetsAbort.signal);return noteContext;
  });
  await app.inspect(tab);
  assert.equal(reads,1);assert.equal(requests[0].context,noteContext);
  assert.equal(tab.state.result,result);assert.equal(tab.state.parsing,false);
});
test('XHS a briefly empty page is polled rather than immediately fetched again',async()=>{
  let reads=0;
  const {app,tab,requests}=inspectionFixture(async()=>++reads===1?null:noteContext);
  await app.inspect(tab);
  assert.equal(reads,2);assert.equal(requests.length,1);assert.equal(requests[0].context,noteContext);
});
test('XHS a page that stays empty falls back after a bounded wait',async()=>{
  let reads=0;
  const {app,tab,requests}=inspectionFixture(async()=>{reads++;return null;});
  const started=Date.now();await app.inspect(tab);
  assert.ok(Date.now()-started<2000);assert.ok(reads<=10);
  assert.equal(requests.length,1);assert.equal(requests[0].context,null);
});
test('XHS navigation during the page grace does not launch a stale worker',async()=>{
  const {app,tab,requests}=inspectionFixture(async()=>{tab.sequence++;return null;});
  await app.inspect(tab);
  assert.equal(requests.length,0);assert.equal(tab.state.result,null);
});
test('XHS video context does not wait for an image list',async()=>{
  let reads=0;const context={...noteContext,note:{type:'video',video:{present:true},imageList:[]}};
  const {app,tab,requests}=inspectionFixture(async()=>{reads++;return context;});
  await app.inspect(tab);assert.equal(reads,1);assert.equal(requests[0].context,context);
});

test('actual page reader has a bounded fallback even if frame execution never settles',async()=>{
  let timerCount=0;
  const {app,tab}=inspectionFixture();
  tab.view.webContents.isDestroyed=()=>false;
  tab.view.webContents.mainFrame={executeJavaScript:()=>{timerCount++;return new Promise(()=>{});}};
  const started=Date.now();
  const result=await CollectorApp.prototype.readPage.call(app,'null',tab,true,tab.assetsAbort.signal,
    {timeoutMs:25,fallback:true});
  assert.equal(result,null);assert.equal(timerCount,1);assert.ok(Date.now()-started<500);
});
test('page fallback cannot swallow navigation cancellation',async()=>{
  const {app,tab}=inspectionFixture();
  tab.view.webContents.isDestroyed=()=>false;
  tab.view.webContents.mainFrame={executeJavaScript:()=>new Promise(()=>{})};
  const result=CollectorApp.prototype.readPage.call(app,'null',tab,true,tab.assetsAbort.signal,
    {timeoutMs:1000,fallback:true});
  tab.assetsAbort.abort();
  await assert.rejects(result,error=>error.cancelled);
});
test('Bilibili passes ready browser data and uses a short fallback budget for missing data',async()=>{
  const loaded={videoId:'BV1234567890',part:1};
  const {app,tab,requests}=inspectionFixture(async(_script,_tab,wait,signal,options)=>{
    assert.equal(wait,true);assert.equal(signal,tab.assetsAbort.signal);
    assert.equal(options.timeoutMs,1200);assert.equal(options.fallback,true);return loaded;
  });
  const url='https://www.bilibili.com/video/BV1234567890?p=1';
  tab.view.webContents.getURL=()=>url;tab.documentKey='bilibili:BV1234567890:1';
  await app.inspect(tab);
  assert.equal(requests.length,1);assert.equal(requests[0].context,loaded);
});
test('direct Vimeo inspection reads the existing player and freezes it for download',async()=>{
  const {app,tab,requests}=inspectionFixture();
  const url='https://vimeo.com/456/abcdef', player='https://player.vimeo.com/video/456?h=abcdef';
  let reads=0;
  const frame={url:player,executeJavaScript:async()=>{
    reads++;return {video:{id:'456'},request:{files:{progressive:[]}}};
  }};
  Object.assign(tab.view.webContents,{getURL:()=>url,isDestroyed:()=>false,
    mainFrame:{executeJavaScript:async()=>1,framesInSubtree:[frame]}});
  await app.inspect(tab);
  assert.equal(reads,1);assert.equal(requests.length,1);
  assert.equal(requests[0].context.vimeo.sourceUrl,url);
  assert.equal(app.cache.get(tab.state.inspectionId).context,requests[0].context);
});

test('actual task renderer inserts stage, progress and task log without rebuilding old state', () => {
  const source=fs.readFileSync(path.resolve(__dirname,'../ui/app.js'),'utf8');
  const start=source.indexOf('function renderTasks() {'),end=source.indexOf('\nfunction render(next)',start);
  assert.ok(start>=0 && end>start);
  const target={},view={tasks:[{id:'fixture',title:'fixture image',status:'running',
    detail:'image-connecting',percent:null,logs:['正在确认网络与系统代理设置…']}]};
  const context={$:()=>target,state:view,openedLogs:new Set(),folderErrors:new Map(),openingFolders:new Set(),clearingHistory:false,escape:text=>String(text ?? ''),
    window:{collectorRenderTasks:(container,tasks,options)=>{
      assert.equal(container,target);assert.equal(tasks,view.tasks);assert.ok(options.openedLogs instanceof Set);
      target.progress=taskProgress(tasks[0]);target.logs=tasks[0].logs;
    }}};
  vm.runInNewContext(source.slice(start,end)+'\nrenderTasks();',context);
  assert.equal(target.progress.stage,'正在连接图片');assert.equal(target.progress.text,'等待数据');
  assert.equal(target.progress.attribute,'');assert.match(target.logs[0],/确认网络与系统代理/);
  view.tasks[0].detail='image-receiving';view.tasks[0].percent=37;
  vm.runInNewContext('renderTasks();',context);
  assert.equal(target.progress.attribute,' value="37"');assert.equal(target.progress.text,'37%');
  const html=fs.readFileSync(path.resolve(__dirname,'../ui/index.html'),'utf8');
  assert.ok(html.indexOf('task-progress.js')<html.indexOf('src="app.js"'));
});
