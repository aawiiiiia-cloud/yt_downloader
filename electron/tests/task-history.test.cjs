'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { openFolder } = require('../task-history.cjs');
const { DownloadQueue } = require('../model.cjs');
const tick = () => new Promise(resolve => setImmediate(resolve));

test('clearing terminal records preserves running, cancelling and queued jobs and their handles', async () => {
  let changed = 0; const jobs = [];
  const queue = new DownloadQueue(() => {
    const job = { cancelled: false }; jobs.push(job);
    return { promise: new Promise(resolve => { job.resolve = resolve; }), cancel() { job.cancelled = true; } };
  }, () => changed++, { globalConcurrency: 2, siteConcurrency: 2 });
  const first = queue.add({url:'https://vimeo.com/1'},'first');
  const second = queue.add({url:'https://vimeo.com/2'},'second');
  const waiting = queue.add({url:'https://vimeo.com/3'},'waiting');
  queue.cancel(second);
  for (const status of ['completed','failed','cancelled']) queue.tasks.unshift({id:status,status});
  assert.equal(queue.clearFinished(),3); assert.ok(changed > 0);
  assert.equal(queue.tasks.length,3); assert.equal(queue.active.size,2);
  assert.equal(queue.tasks.find(row=>row.id===first).status,'running');
  assert.equal(queue.tasks.find(row=>row.id===second).status,'cancelling');
  assert.equal(queue.tasks.find(row=>row.id===waiting).status,'queued');
  assert.equal(jobs[0].cancelled,false); assert.equal(jobs[1].cancelled,true);
  jobs[0].resolve({}); await tick(); assert.equal(jobs.length,3);
  assert.equal(queue.tasks.find(row=>row.id===waiting).status,'running');
  queue.close(); for (const job of jobs) job.resolve({}); await tick();
});

test('clearing an empty or live-only history is a no-op and cannot start extra workers', () => {
  let changes=0, starts=0;
  const queue=new DownloadQueue(()=>{starts++;return{promise:new Promise(()=>{}),cancel(){}};},()=>changes++);
  assert.equal(queue.clearFinished(),0); assert.equal(changes,0);
  queue.add({url:'https://vimeo.com/1'},'live');const before=changes;
  assert.equal(queue.clearFinished(),0); assert.equal(changes,before); assert.equal(starts,1);
});

test('clearing also removes older terminal records outside the visible history limit', () => {
  const queue=new DownloadQueue(()=>{},()=>{});
  queue.tasks=Array.from({length:100},(_,i)=>({id:String(i),status:i%2?'completed':'failed'}));
  assert.equal(queue.list().length,60);assert.equal(queue.clearFinished(),100);assert.deepEqual(queue.list(),[]);
});

test('existing directories open successfully without treating an empty shell result as failure', async () => {
  const folder=fs.mkdtempSync(path.join(os.tmpdir(),'media-dl-history-'));let opened;
  try { await openFolder(folder,async value=>{opened=value;return '';},true); assert.equal(opened,folder); }
  finally { fs.rmdirSync(folder); }
});

test('missing, moved or non-directory targets never call the system opener', async () => {
  const folder=fs.mkdtempSync(path.join(os.tmpdir(),'media-dl-history-'));
  const file=path.join(folder,'file.txt');fs.writeFileSync(file,'test');let calls=0;
  try {
    for(const target of [undefined,'',path.join(folder,'gone'),file])
      await assert.rejects(openFolder(target,async()=>{calls++;return '';},true),/下载目录不存在.*删除或移动/);
    await assert.rejects(openFolder(path.join(folder,'gone'),async()=>{calls++;},false),/保存目录尚不存在/);
    assert.equal(calls,0);
  } finally { fs.unlinkSync(file);fs.rmdirSync(folder); }
});

test('resolved shell errors and rejected opens become visible safe messages', async () => {
  const folder=fs.mkdtempSync(path.join(os.tmpdir(),'media-dl-history-'));
  try {
    for(const opener of [async()=>'<unsafe OS diagnostic>',async()=>{throw new Error('secret internal diagnostic');}]) {
      await assert.rejects(openFolder(folder,opener,true),error=>/无法打开目录/.test(error.message)&&!error.message.includes('diagnostic'));
    }
  } finally { fs.rmdirSync(folder); }
});

test('access-denied filesystem errors give a permissions message without calling the opener', async t => {
  t.mock.method(fs,'statSync',()=>{throw Object.assign(new Error('private path'),{code:'EACCES'});});
  let calls=0;
  await assert.rejects(openFolder('restricted-folder',async()=>{calls++;},true),/无法访问目录.*访问权限/);
  assert.equal(calls,0);
});
