'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),{EventEmitter}=require('node:events');
const {findDetail,findInFlight,snapshotScript,DouyinPage}=require('../douyin-page.cjs');
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
