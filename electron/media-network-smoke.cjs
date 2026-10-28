'use strict';
// Real Chromium networking against loopback only. protocol.handle mocks skip
// the referrer validation that caused ERR_BLOCKED_BY_CLIENT in production.
const assert=require('node:assert/strict');
const http=require('node:http');
const {session}=require('electron');
const {Assets}=require('./assets.cjs');
async function run(check) {
  const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAE0lEQVR4nGO8/Po+AwwwwVnoHABhQQKjPQkWWQAAAABJRU5ErkJggg==','base64'),seen=[];
  const server=http.createServer((request,response)=>{
    seen.push({method:request.method,referer:request.headers.referer,range:request.headers.range});
    response.writeHead(200,{'content-type':'image/png','content-length':png.length});
    response.end(request.method==='HEAD'?undefined:png);
  });
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  const target=`http://127.0.0.1:${server.address().port}/fixture.png`,web=session.fromPartition('media-network-regression');
  const referrer='http://source.example/item.htm?id=123&skuId=7';
  try {
    await assert.rejects(()=>web.fetch(target,{headers:{Referer:referrer},signal:AbortSignal.timeout(4000)}),/ERR_BLOCKED_BY_CLIENT/);
    check('real Chromium rejects a full cross-origin referrer before it reaches a server',seen.length===0);
    const assets=new Assets((_url,options)=>web.fetch(target,options));
    const preview=await assets.preview('https://img.alicdn.com/fixture.png','taobao',undefined,false,referrer);
    check('the production asset path permits previews using the default browser security policy',preview.dataUrl==='data:image/png;base64,'+png.toString('base64')&&seen[0].referer==='http://source.example/');
    const metadata=await assets.sourceSize(['https://img.alicdn.com/fixture.png'],'taobao',undefined,referrer,true);
    check('an exact-original preview supplies source metadata without a second real request',metadata.size===png.length&&metadata.width===4&&metadata.height===3&&seen.length===1);
    const rangeAssets=new Assets((_url,options)=>web.fetch(target,options));
    const ranged=await rangeAssets.sourceSize(['https://img.alicdn.com/fixture.png'],'taobao',undefined,referrer,true);
    check('one real range request recovers source bytes and pixel dimensions',ranged.size===png.length&&ranged.width===4&&ranged.height===3&&!seen.some(row=>row.method==='HEAD')&&seen.some(row=>row.range==='bytes=0-262143'));
    check('asset requests never send a product path or weaken referrer policy',seen.every(row=>row.referer==='http://source.example/'));
  }finally{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
}
module.exports={run};
