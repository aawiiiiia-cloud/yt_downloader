 'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const { taskProgress, taskSpeed } = require('../ui/task-progress.js');
const { createDownloadFeedback } = require('../ui/download-feedback.js');
const { DownloadQueue } = require('../model.cjs');
const tick = () => new Promise(r => setImmediate(r));
const running = { status:'running',detail:'downloading',speedUpdatedAt:10000,downloadedBytes:1234 };
for (const [value, expected] of [[0,'0 B/s'],[18,'18 B/s'],[1024,'1.0 KB/s'],[1024**2*1.5,'1.5 MB/s'],[1024**3,'1.0 GB/s']]) {
  test(`task speed formats ${value} bytes/sec`, () => assert.equal(taskSpeed({...running,speed:value},11000),expected));
}
for (const value of [null, undefined, NaN, Infinity, -1]) {
  test(`invalid speed ${value} never renders fake or NaN speed`, () => assert.equal(taskSpeed({...running,speed:value},11000),'测速中'));
}
test('speed expires after six seconds and non-transfer phases discard stale numbers', () => {
  const task={...running,speed:1024};
  assert.equal(taskSpeed(task,15999),'1.0 KB/s');assert.equal(taskSpeed(task,16000),'等待数据');
  for (const detail of ['preparing','extracting','image-connecting','video-connecting'])
    assert.equal(taskSpeed({...task,detail},11000),'等待连接');
  for (const detail of ['image-validating','image-converting','processing','finished'])
    assert.ok(!taskSpeed({...task,detail},11000).includes('/s'));
  for (const status of ['completed','queued','failed','cancelled']) assert.equal(taskSpeed({...task,status},11000),'');
  assert.equal(taskSpeed({...task,status:'cancelling'},11000),'正在取消');
  assert.equal(taskProgress({status:'running',percent:null,downloadedBytes:1024**2}).text,'已接收 1.0 MB');
});
for (const site of ['youtube','bilibili','xiaohongshu','vimeo','douyin','behance','taobao','zcool']) {
  test(`${site} queue preserves real speed, zero and ETA; clears stages and final state`, async () => {
    let emit, finish;
    const queue=new DownloadQueue((_s,cb)=>{emit=cb;return {promise:new Promise(r=>finish=r),cancel(){}};},()=>{});
    queue.add({url:`https://www.${site}.com/fixture`},site);const task=queue.tasks[0];
    assert.equal(task.speed,null);
    emit('progress',{status:'downloading',percent:null,speed:2*1024**2,eta:35,downloadedBytes:65536});
    assert.equal(task.speed,2097152);assert.equal(task.eta,35);assert.equal(task.percent,null);assert.ok(task.speedUpdatedAt>0);
    emit('progress',{status:'image-receiving',speed:0});assert.equal(task.speed,0);
    emit('progress',{status:'image-validating',speed:100});assert.equal(task.speed,null);assert.equal(task.eta,null);
    emit('progress',{status:'downloading',speed:NaN});assert.equal(task.speed,null);
    emit('progress',{status:'downloading',speed:100});finish({});await tick();
    assert.equal(task.status,'completed');assert.equal(task.speed,null);assert.equal(task.speedUpdatedAt,null);
  });
}
function fixtureHost(reduced=false) {
  const events=new Map(),timers=new Map(),icons=[],animations=[];let next=0;
  function node(rect={left:210,right:310,top:30,width:100,height:40}) {
    const classes=new Set();
    return {isConnected:true,offsetWidth:32,classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)},
      getBoundingClientRect:()=>rect,setAttribute(){},remove(){this.isConnected=false;},
      animate(frames,options){let resolve,reject;const row={frames,options,finished:new Promise((r,j)=>{resolve=r;reject=j;}),finish:()=>resolve(),cancel:()=>reject(new Error('cancelled'))};animations.push(row);return row;}};
  }
  const host={innerWidth:400,innerHeight:720,document:{body:{animate(){},appendChild(n){icons.push(n);}},createElement:()=>node()},
    matchMedia:()=>({matches:reduced}),setTimeout:(fn)=>{const id=++next;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id),
    addEventListener:(name,fn)=>{if(!events.has(name))events.set(name,new Set());events.get(name).add(fn);},removeEventListener:(name,fn)=>events.get(name)?.delete(fn)};
  return {host,target:node(),icons,animations,timers,dispatch:name=>{for(const fn of [...events.get(name)||[]])fn();}};
}
const origin={left:270,right:350,top:400,width:80,height:40};
test('receipt flies to tasks-tab center and cleans its icon without adding a target highlight', async () => {
  const f=fixtureHost(),feedback=createDownloadFeedback(f.host),p=feedback.fly(origin,f.target);
  assert.equal(f.icons.length,1);assert.equal(f.target.classList.contains('download-arrived'),false);
  const a=f.animations[0];assert.equal(a.options.duration,780);assert.equal(a.frames[0].opacity,0);
  assert.ok(a.frames.some(k=>k.transform.includes('scale(1.15)')));
  assert.equal(a.frames.at(-1).transform,'translate(244px, 34px) scale(0.3)');
  a.finish();await p;assert.equal(f.icons[0].isConnected,false);assert.equal(f.target.classList.contains('download-arrived'),false);
  feedback.clear();assert.equal(f.timers.size,0);assert.equal(f.target.classList.contains('download-arrived'),false);
});
test('reduced motion and missing animation API skip flying without adding a target highlight', async () => {
  for(const reduced of [true,false]) {
    const f=fixtureHost(reduced);if(!reduced)delete f.host.document.body.animate;
    const feedback=createDownloadFeedback(f.host);await feedback.fly(origin,f.target);
    assert.equal(f.icons.length,0);assert.equal(f.target.classList.contains('download-arrived'),false);feedback.clear();
  }
});
test('resize/pagehide/animation rejection and invisible target never leak an overlay', async () => {
  for(const action of ['resize','pagehide','reject','invisible']) {
    const f=fixtureHost();if(action==='invisible')f.target.getBoundingClientRect=()=>({width:0,height:0});
    const feedback=createDownloadFeedback(f.host),p=feedback.fly(origin,f.target);
    if(action==='resize'||action==='pagehide')f.dispatch(action);
    if(action==='reject')f.animations[0].cancel();
    await p;assert.ok(f.icons.every(i=>!i.isConnected));assert.equal(f.timers.size,0);feedback.clear();
  }
});
test('rapid independent receipts are bounded; disposal resolves all pending flights', async () => {
  const f=fixtureHost(),feedback=createDownloadFeedback(f.host);const all=[];
  for(let i=0;i<5;i++)all.push(feedback.fly(origin,f.target));
  assert.equal(f.icons.filter(i=>i.isConnected).length,3);feedback.clear();await Promise.all(all);assert.equal(f.timers.size,0);
});
const source=fs.readFileSync(path.resolve(__dirname,'../ui/app.js'),'utf8');
const start=source.indexOf("  const imageButton = event.target.closest('[data-download-image]');"),end=source.indexOf('\n});',start);
assert.ok(start>0&&end>start);
function clickFixture({single=false, fail=false, switchPanel=false, changeReceipt=false, retryLogin=false}={}) {
  const actions=[],button={dataset:{downloadImage:'7'},getBoundingClientRect:()=>{actions.push('origin');return origin;}},target={};
  const context={state:{inspectionId:'receipt'},selectedItem:0,panel:'resources',pendingDownloads:new Map(),
    event:{preventDefault(){},target:{id:single?'single':'download',closest:()=>single?button:null}},
    $:id=>id==='download'?button:id==='tasks-tab'?target:null,document:{querySelectorAll:()=>[{dataset:{image:'7'}}]},
    updateMediaInfo(){actions.push('update');},notify(message){actions.push(message);},showPanel(name){actions.push(name);context.panel=name;},
    window:{collector:{call:async method=>{actions.push(method);if(method==='login')return;
      if(fail)throw new Error('fixture failure');if(retryLogin&&!actions.includes('login'))throw Object.assign(new Error('auth'),{code:'YOUTUBE_AUTH_REQUIRED'});return 'task-id';}},
      collectorDownloadFeedback:{fly:async(rect,tab)=>{assert.equal(rect,origin);assert.equal(tab,target);actions.push('fly');if(switchPanel)context.panel='settings';if(changeReceipt)context.state.inspectionId='new';}}},};
  return {context,actions,button,run:()=>vm.runInNewContext('(async()=>{'+source.slice(start,end)+'})()',context)};
}
test('actual download handler captures origin before awaiting and flies only after successful queue receipt', async () => {
  for(const single of [false,true]) {
    const f=clickFixture({single});await f.run();assert.ok(f.actions.indexOf('origin')<f.actions.indexOf('download'));
    assert.ok(f.actions.indexOf('download')<f.actions.indexOf('fly'));assert.equal(f.actions.includes('tasks'),false);assert.equal(f.context.panel,'resources');assert.equal(f.context.pendingDownloads.size,0);
  }
  const failed=clickFixture({fail:true});await failed.run();assert.equal(failed.actions.includes('fly'),false);assert.ok(failed.actions.includes('fixture failure'));
  const auth=clickFixture({retryLogin:true});await auth.run();assert.equal(auth.actions.filter(a=>a==='fly').length,1);assert.ok(auth.actions.includes('login'));
});
test('actual download handler never steals tab after user navigation and suppresses duplicate requests', async () => {
  for(const setting of [{switchPanel:true},{changeReceipt:true}]) {const f=clickFixture(setting);await f.run();assert.equal(f.actions.includes('tasks'),false);assert.equal(f.context.panel,setting.switchPanel?'settings':'resources');}
  const f=clickFixture();f.context.pendingDownloads.set(JSON.stringify(['receipt',0,null]),{});await f.run();assert.equal(f.actions.includes('download'),false);assert.equal(f.actions.includes('fly'),false);
});

test('different image buttons submit together while the same pending button cannot submit twice', async () => {
  const f=clickFixture({single:true}), requests=[];
  f.context.window.collector.call=async()=>{const next=Promise.withResolvers();requests.push(next);return next.promise;};
  const first=f.run();await f.run();assert.equal(requests.length,1);
  const secondButton={...f.button,dataset:{downloadImage:'8'}};
  f.context.event.target.closest=()=>secondButton;const second=f.run();assert.equal(requests.length,2);
  assert.equal(f.context.pendingDownloads.size,2);requests[0].resolve('first');await first;
  assert.equal(f.context.pendingDownloads.size,1);requests[1].resolve('second');await second;
  assert.equal(f.context.pendingDownloads.size,0);
});

test('queue receipt unlocks before animation and an old animation cannot clear a newer request', async () => {
  const f=clickFixture({single:true}), requests=[],flights=[];
  f.context.window.collector.call=async()=>{const p=Promise.withResolvers();requests.push(p);return p.promise;};
  f.context.window.collectorDownloadFeedback.fly=async()=>{const p=Promise.withResolvers();flights.push(p);return p.promise;};
  const first=f.run();requests[0].resolve('first');await tick();
  assert.equal(f.context.pendingDownloads.size,0);assert.equal(flights.length,1);
  const second=f.run();assert.equal(f.context.pendingDownloads.size,1);
  flights[0].resolve();await first;assert.equal(f.context.pendingDownloads.size,1);
  requests[1].reject(new Error('second request failed'));await second;assert.equal(f.context.pendingDownloads.size,0);
});

test('pending batch submission does not disable single-image submission and vice versa', async () => {
  const f=clickFixture(),requests=[];
  f.context.window.collector.call=async()=>{const p=Promise.withResolvers();requests.push(p);return p.promise;};
  const batch=f.run();f.context.event.target.id='single';f.context.event.target.closest=()=>f.button;
  const single=f.run();assert.equal(requests.length,2);assert.equal(f.context.pendingDownloads.size,2);
  requests.forEach((p,i)=>p.resolve('task-'+i));await Promise.all([batch,single]);
});

test('actual button renderer locks only its own request in the selected resource', () => {
  const begin=source.indexOf('function updateMediaInfo() {'),end=source.indexOf('\nfunction showPanel(',begin);
  const buttons=[1,2].map(index=>({dataset:{downloadImage:String(index)}})),batch={};
  const pending=new Map([[JSON.stringify(['receipt',0,1]),{}]]);
  const context={state:{inspectionId:'receipt',result:{site:'xiaohongshu',items:[{kind:'images'}]},settings:{}},selectedItem:0,
    pendingDownloads:pending,$:id=>id==='download'?batch:null,document:{querySelectorAll:()=>buttons},setLoading(){}};
  const render=()=>vm.runInNewContext(source.slice(begin,end)+'\nupdateMediaInfo();',context);
  render();assert.equal(buttons[0].disabled,true);assert.equal(buttons[1].disabled,false);assert.equal(batch.disabled,false);
  pending.set(JSON.stringify(['receipt',0,null]),{});render();assert.equal(batch.disabled,true);assert.equal(buttons[1].disabled,false);
  context.selectedItem=1;context.state.result.items.push({kind:'images'});render();assert.ok(buttons.every(b=>!b.disabled));assert.equal(batch.disabled,false);
});
test('speed ticker updates labels only; feedback script obeys existing CSP and load order', () => {
  const html=fs.readFileSync(path.resolve(__dirname,'../ui/index.html'),'utf8');
  assert.ok(html.indexOf('download-feedback.js')<html.indexOf('src="app.js"'));assert.ok(html.includes("script-src 'self'"));
  const begin=source.indexOf('const speedTimer ='),last=source.indexOf("window.collector.onState(render);",begin),label={dataset:{taskSpeed:'fixture'},textContent:''};let refresh;
  vm.runInNewContext(source.slice(begin,last),{state:{tasks:[{id:'fixture',...running,speed:1024,speedUpdatedAt:Date.now()}]},
    $:()=>({hidden:false}),document:{querySelectorAll:()=>[label]},window:{setInterval:fn=>{refresh=fn;return 1;},clearInterval(){},addEventListener(){},collectorTaskSpeed:taskSpeed}});
  refresh();assert.equal(label.textContent,'1.0 KB/s');
});
