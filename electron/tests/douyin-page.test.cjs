'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),{EventEmitter}=require('node:events');
const {findDetail,findDetails,findInFlight,snapshotScript,DouyinPage}=require('../douyin-page.cjs');
const detail={aweme_id:'123',desc:'受控视频',author:{nickname:'作者',private:'not needed'},video:{duration:58000,width:640,height:360,play_addr:{url_list:['https://fixture.douyinvod.com/v.mp4']},cover:{url_list:['https://p3.douyinpic.com/a.jpg']}}};
test('Douyin snapshots bind the video ID and strip unrelated account state',()=>{
  const root={login:{sessionid:'must-not-leak'},aweme:{detail}};
  const result=findDetail(root,'123');assert.equal(result.aweme_id,'123');
  assert.ok(!JSON.stringify(result).includes('must-not-leak'));assert.ok(!JSON.stringify(result).includes('not needed'));
  assert.equal(findDetail(root,'456'),null);
  const cycle={};cycle.self=cycle;assert.equal(findDetail(cycle,'123'),null);
});
test('React flight data is parsed as JSON with bounded references, never executed',()=>{
  const records=[[1,'1:{"aweme_id":"123","video":"$2"}\n2:'+JSON.stringify(detail.video)+'\n']];
  assert.equal(findInFlight(records,'123').video.duration,58000);
  assert.equal(findInFlight([[1,'1:{"aweme_id":"123","video":"$1"}\n']],'456'),null);
  assert.equal(findInFlight([[1,'1:process.exit(1)\n']],'123'),null);
});
test('browser snapshot reads both encoded render data and serialized flight scripts',()=>{
  const make=(render,scripts)=>({URL,location:{href:'https://www.douyin.com/?modal_id=123'},window:{},document:{getElementById:()=>render?{textContent:render}:null,querySelectorAll:()=>scripts.map(textContent=>({textContent}))}});
  const rendered=vm.runInNewContext(snapshotScript,make(encodeURIComponent(JSON.stringify({aweme:{detail}})),[]));
  assert.equal(rendered.videoId,'123');
  const payload=[1,'1:'+JSON.stringify(detail)+'\n'];
  const flight=vm.runInNewContext(snapshotScript,make(null,['self.__pace_f.push('+JSON.stringify(payload)+');']));
  assert.equal(flight.videoId,'123');assert.equal(flight.detail.video.height,360);
});
test('network observation reads only matching Douyin JSON responses and respects existing debuggers',async()=>{
  const debuggerApi=new EventEmitter();let attached=false,reads=0;
  Object.assign(debuggerApi,{isAttached:()=>attached,attach:()=>{attached=true},detach:()=>{attached=false},sendCommand:async method=>{if(method==='Network.getResponseBody'){reads++;return {body:JSON.stringify({aweme_detail:detail})}}}});
  let url='https://www.douyin.com/video/123';const web={debugger:debuggerApi,isDestroyed:()=>false,getURL:()=>url};
  const page=new DouyinPage(web);
  await page.message('Network.responseReceived',{requestId:'no',response:{url:'https://evil.invalid/aweme/v1/web/aweme/detail/',status:200,mimeType:'application/json'}});
  await page.message('Network.loadingFinished',{requestId:'no',encodedDataLength:100});assert.equal(reads,0);
  await page.message('Network.responseReceived',{requestId:'yes',response:{url:'https://www.douyin.com/aweme/v1/web/aweme/detail/',status:200,mimeType:'application/json'}});
  await page.message('Network.loadingFinished',{requestId:'yes',encodedDataLength:100});assert.equal(page.get(url).videoId,'123');
  url='https://www.douyin.com/video/456';assert.equal(page.get(url),null);page.dispose();assert.equal(attached,false);
  attached=true;new DouyinPage(web).dispose();assert.equal(attached,true);
});
test('an incomplete modal placeholder cannot hide the playable video in a feed',()=>{
  const placeholder={...detail,video:{cover:detail.video.cover,duration:58000}};
  assert.equal(findDetail(placeholder,'123'),null);
  assert.ok(findDetail({items:[placeholder,detail]},'123').video.play_addr);
  assert.deepEqual(findDetails({items:[{id:'456',video:{bitRateList:'invalid'}},detail]}).map(v=>v.aweme_id),['123']);
});
test('feed videos received before a modal opens remain available after SPA navigation',async t=>{
  const api=new EventEmitter();let url='https://www.douyin.com/jingxuan';
  const second={...detail,aweme_id:'456',desc:'第二条视频'};
  Object.assign(api,{isAttached:()=>false,attach(){},detach(){},sendCommand:async method=>method==='Network.getResponseBody'?{body:JSON.stringify({aweme_list:[detail,second],account:{sessionid:'never cache'}})}:{}});
  const page=new DouyinPage({debugger:api,getURL:()=>url,isDestroyed:()=>false});
  await page.message('Network.responseReceived',{requestId:'feed',response:{url:'https://www.douyin.com/aweme/v1/web/tab/feed/',status:200,mimeType:'application/json'}});
  await page.message('Network.loadingFinished',{requestId:'feed',encodedDataLength:1000});
  url='https://www.douyin.com/jingxuan?modal_id=456';
  assert.equal(page.get(url).detail.desc,'第二条视频');
  assert.equal(page.get('https://www.douyin.com/video/123').videoId,'123');
  assert.equal(page.get('https://www.douyin.com/video/789'),null);
  assert.ok(!JSON.stringify([...page.cache.values()]).includes('never cache'));
  const now=Date.now();t.mock.method(Date,'now',()=>now+300001);assert.equal(page.get(url),null);page.dispose();
});
test('responses from a previous document cannot refill the cache after reset',async()=>{
  const api=new EventEmitter();let release;
  Object.assign(api,{isAttached:()=>false,attach(){},detach(){},sendCommand:method=>method==='Network.getResponseBody'?new Promise(resolve=>release=resolve):Promise.resolve({})});
  const page=new DouyinPage({debugger:api,getURL:()=>'',isDestroyed:()=>false});
  await page.message('Network.responseReceived',{requestId:'feed',response:{url:'https://www.douyin.com/aweme/v1/web/feed/',status:200,mimeType:'application/json'}});
  const reading=page.message('Network.loadingFinished',{requestId:'feed',encodedDataLength:1000});
  page.reset();release({body:JSON.stringify({aweme_list:[detail]})});await reading;
  assert.equal(page.cache.size,0);page.dispose();
});
test('feed caching remains bounded and reset drops previously prefetched videos',()=>{
  const api=new EventEmitter();Object.assign(api,{isAttached:()=>true});
  const page=new DouyinPage({debugger:api,isDestroyed:()=>false});
  for(let i=1;i<=30;i++)page.remember({...detail,aweme_id:String(i)});
  assert.equal(page.cache.size,24);assert.equal(page.get('https://www.douyin.com/video/1'),null);
  assert.equal(page.get('https://www.douyin.com/video/30').videoId,'30');
  page.reset();assert.equal(page.get('https://www.douyin.com/video/30'),null);page.dispose();
});
