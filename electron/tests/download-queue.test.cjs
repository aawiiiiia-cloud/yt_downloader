'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { SITES, DownloadQueue, concurrencySettings, downloadTargetKey } = require('../model.cjs');
const tick = () => new Promise(resolve => setImmediate(resolve));
function controlled(settings) {
  const started = [], handles = new Map();
  const queue = new DownloadQueue(spec => {
    started.push(spec.name);
    const handle = { cancelled:false, cancel(){this.cancelled=true;} };
    handle.promise = new Promise((resolve,reject) => Object.assign(handle,{resolve,reject}));
    handles.set(spec.name,handle); return handle;
  }, () => {}, settings);
  const add = (name, site='youtube', extra={}) => queue.add({name,url:SITES[site].home+'?fixture='+name,...extra},name);
  return {queue,started,handles,add};
}
test('defaults start five sites but only one task per site', () => {
  const {queue,add,started} = controlled();
  for (const site of Object.keys(SITES)) add(site,site);
  add('second-youtube');
  assert.equal(queue.active.size,5); assert.equal(started.length,5);
  assert.equal(queue.tasks.filter(task=>task.status==='queued').length,Object.keys(SITES).length+1-5);
  assert.deepEqual(queue.limits,{globalConcurrency:5,siteConcurrency:1});
});

test('different ZCOOL videos use the configured site allowance while duplicate outputs still serialize', () => {
  const {queue,add,started}=controlled({globalConcurrency:5,siteConcurrency:2});
  const base={url:'https://www.zcool.com.cn/work/ZNzQyNDUwOTY=.html',kind:'video'};
  add('v1','zcool',{...base,mediaId:'video-1'});add('duplicate','zcool',{...base,mediaId:'video-1'});
  add('v2','zcool',{...base,mediaId:'video-2'});add('v3','zcool',{...base,mediaId:'video-3'});
  assert.deepEqual(started,['v1','v2']);assert.equal(queue.active.size,2);
  assert.equal(queue.tasks.filter(row=>row.status==='queued').length,2);
});
test('a full site does not block another site; eligible jobs retain FIFO order', async () => {
  const {add,started,handles,queue} = controlled({globalConcurrency:3,siteConcurrency:1});
  add('a1');add('a2');add('a3');add('b1','bilibili');
  assert.deepEqual(started,['a1','b1']);
  handles.get('a1').resolve({});await tick();
  assert.deepEqual(started,['a1','b1','a2']);assert.equal(queue.active.size,2);
});
test('global capacity is shared by every site even when individual sites have room', async () => {
  const {add,started,handles,queue} = controlled({globalConcurrency:2,siteConcurrency:2});
  add('a1');add('a2');add('b1','bilibili');
  assert.deepEqual(started,['a1','a2']);
  handles.get('a1').resolve({});await tick();
  assert.deepEqual(started,['a1','a2','b1']);assert.equal(queue.active.size,2);
});
test('Taobao, Tmall and Tmall supermarket share one site allowance', async () => {
  const {add,started,handles} = controlled();
  add('taobao','taobao',{url:'https://item.taobao.com/item.htm?id=1'});
  add('tmall','taobao',{url:'https://detail.tmall.com/item.htm?id=2'});
  add('market','taobao',{url:'https://chaoshi.detail.tmall.com/item.htm?id=3'});
  add('bili','bilibili');
  assert.deepEqual(started,['taobao','bili']);
  handles.get('taobao').resolve({});await tick();assert.deepEqual(started,['taobao','bili','tmall']);
});
test('raising limits starts waiting jobs; lowering limits does not cancel active work', async () => {
  const {add,queue,started,handles} = controlled({globalConcurrency:1,siteConcurrency:1});
  add('a1');add('a2');add('b1','bilibili');add('b2','bilibili');
  queue.setLimits({globalConcurrency:3,siteConcurrency:2});assert.deepEqual(started,['a1','a2','b1']);
  queue.setLimits({globalConcurrency:1,siteConcurrency:1});
  assert.equal(queue.active.size,3);assert.ok([...handles.values()].every(h=>!h.cancelled));
  handles.get('a1').resolve({});await tick();assert.equal(started.length,3);
  handles.get('a2').resolve({});await tick();assert.equal(started.length,3);
  handles.get('b1').resolve({});await tick();assert.deepEqual(started,['a1','a2','b1','b2']);
});
test('a cancelling task holds its slot until its process actually settles', async () => {
  const {add,queue,started,handles} = controlled({globalConcurrency:1,siteConcurrency:1});
  const active=add('a1'), waiting=add('a2');queue.cancel(active);
  assert.ok(handles.get('a1').cancelled);assert.equal(queue.tasks.find(t=>t.id===active).status,'cancelling');
  assert.deepEqual(started,['a1']);handles.get('a1').resolve({});await tick();
  assert.deepEqual(started,['a1','a2']);assert.equal(queue.tasks.find(t=>t.id===active).status,'cancelled');
  assert.equal(queue.tasks.find(t=>t.id===waiting).status,'running');
});
test('queued cancellation, failures, retry and shutdown remain isolated across running tasks', async () => {
  const {add,queue,started,handles} = controlled({globalConcurrency:2,siteConcurrency:1});
  const first=add('a1'), second=add('b1','bilibili'), cancelled=add('a2');
  queue.cancel(cancelled);handles.get('a1').reject(new Error('Read timed out'));await tick();
  assert.equal(queue.tasks.find(t=>t.id===first).failure.code,'NETWORK_TIMEOUT');
  assert.equal(queue.tasks.find(t=>t.id===second).status,'running');assert.deepEqual(started,['a1','b1']);
  queue.retry(first);assert.deepEqual(started,['a1','b1','a1']);
  add('b2','bilibili');queue.close();
  assert.ok(handles.get('a1').cancelled&&handles.get('b1').cancelled);
  handles.get('a1').resolve({});handles.get('b1').resolve({});await tick();assert.equal(queue.active.size,0);
  assert.ok(queue.tasks.every(t=>!['queued','running','cancelling'].includes(t.status)));
});
test('same output is serialized while other content and separate save folders can run', async () => {
  const {add,queue,started,handles} = controlled({globalConcurrency:5,siteConcurrency:5});
  const url='https://www.youtube.com/watch?v=same';
  add('original','youtube',{url,settings:{saveDir:'same-output'}});
  add('duplicate','youtube',{url:url+'&tracking=1',settings:{saveDir:'same-output'}});
  add('different-folder','youtube',{url,settings:{saveDir:'other-output'}});
  add('other-video','youtube',{url:'https://www.youtube.com/watch?v=other',settings:{saveDir:'same-output'}});
  assert.deepEqual(started,['original','different-folder','other-video']);
  handles.get('original').resolve({});await tick();assert.equal(started.at(-1),'duplicate');assert.equal(queue.active.size,3);
});
test('image jobs for the same product category are serialized without locking another category', () => {
  const {add,started} = controlled({globalConcurrency:5,siteConcurrency:5});
  const url='https://detail.tmall.com/item.htm?id=123';
  add('main1','taobao',{url,kind:'images',mediaId:'main',indices:[1]});
  add('main2','taobao',{url,kind:'images',mediaId:'main',indices:[2]});
  add('sku','taobao',{url,kind:'images',mediaId:'sku',indices:[1]});
  assert.deepEqual(started,['main1','sku']);
});
test('alternate parts and Vimeo access hashes cannot race the same video output', () => {
  for(const [a,b] of [['https://www.bilibili.com/video/BVfixture?p=1','https://www.bilibili.com/video/BVfixture?p=2'],
    ['https://vimeo.com/123/abc','https://vimeo.com/123/def']])
    assert.equal(downloadTargetKey({url:a}),downloadTargetKey({url:b}));
});
test('live and queued tasks remain in history after sixty newer completions', () => {
  const {add,queue} = controlled();const id=add('long-running');add('waiting');
  for(let i=0;i<80;i++)queue.tasks.unshift({id:'finished-'+i,status:'completed',title:'done',percent:100});
  const list=queue.list();assert.equal(list.length,60);assert.ok(list.some(t=>t.id===id));
  assert.ok(list.some(t=>t.status==='queued'));assert.ok(list.every(t=>!t.spec&&!t.process));
});
test('saved limits recover defaults; invalid input never changes an existing schedule', () => {
  assert.deepEqual(concurrencySettings({globalConcurrency:-1,siteConcurrency:'2'}),{globalConcurrency:5,siteConcurrency:1});
  const {queue}=controlled();
  for(const value of [0,-1,1.5,21,NaN,Infinity,'2',null]){
    assert.throws(()=>queue.setLimits({globalConcurrency:value,siteConcurrency:1}),/全局并发/);
    assert.throws(()=>queue.setLimits({globalConcurrency:5,siteConcurrency:value}),/单站并发/);
    assert.deepEqual(queue.limits,{globalConcurrency:5,siteConcurrency:1});
  }
});
