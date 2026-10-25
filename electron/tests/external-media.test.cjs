'use strict';
const test=require('node:test'), assert=require('node:assert/strict'),vm=require('node:vm');
const {externalUrl,externalEntries,collectVimeo,vimeoConfigScript}=require('../external-media.cjs');
const source='https://www.behance.net/gallery/123/Test';
test('embed routing retains unlisted access and rejects foreign or malformed player URLs',()=>{
  assert.deepEqual(externalUrl('https://player.vimeo.com/video/123?h=abcdef&amp;autoplay=1'),{provider:'vimeo',id:'123',url:'https://player.vimeo.com/video/123?h=abcdef'});
  assert.equal(externalUrl('https://www.youtube-nocookie.com/embed/SmQd5BgLmd0').url,'https://www.youtube.com/watch?v=SmQd5BgLmd0');
  for(const url of ['http://player.vimeo.com/video/123','https://vimeo.com.evil.invalid/123','https://user:secret@player.vimeo.com/video/123','https://127.0.0.1/video/123','https://player.vimeo.com/video/123?h=../../'])assert.equal(externalUrl(url),null);
});
test('external modules bind identity to the frozen project and retain duplicate-player modules',()=>{
  const context={projectId:'123',project:{id:123,modules:[{id:8,__typename:'EmbedModule',originalEmbed:'<iframe src="https://player.vimeo.com/video/456">'},{id:9,__typename:'VideoModule',embed:'<iframe src="https://player.vimeo.com/video/456">'}]}};
  assert.deepEqual(externalEntries(context).map(row=>row.key),['8-0','9-1']);
  context.projectId='999';assert.deepEqual(externalEntries(context),[]);
});
test('player reader returns only media fields, never account or session configuration',()=>{
  const result=vm.runInNewContext(vimeoConfigScript,{playerConfig:{video:{id:456,title:'video',owner:{name:'author',token:'secret-owner'}},request:{files:{progressive:[]},session:'secret-session'},account:{token:'secret-token'}}});
  assert.equal(result.video.id,456);assert.ok(!JSON.stringify(result).includes('secret'));
});
function page(read){return {isDestroyed:()=>false,getURL:()=>source,mainFrame:{executeJavaScript:async()=>{},framesInSubtree:[{url:'https://player.vimeo.com/video/456',executeJavaScript:read}]}};}
const entries=[{provider:'vimeo',id:'456',key:'8-0',url:'https://player.vimeo.com/video/456'}];
test('browser collection matches video and access hash before accepting a configuration',async()=>{
  const web=page(async()=>({video:{id:456},request:{files:{progressive:[]}}}));
  assert.equal((await collectVimeo(web,source,entries,{timeout:0})).size,1);
  assert.equal((await collectVimeo(web,source,[{...entries[0],url:entries[0].url+'?h=abcdef'}],{timeout:0})).size,0);
  web.mainFrame.framesInSubtree[0].executeJavaScript=async()=>({video:{id:999},request:{files:{}}});
  assert.equal((await collectVimeo(web,source,entries,{timeout:0})).size,0);
});
test('navigation and cancellation discard late iframe responses without reading another project',async()=>{
  const controller=new AbortController();let finish;
  const web=page(()=>new Promise(resolve=>{finish=resolve;}));
  const pending=collectVimeo(web,source,entries,{signal:controller.signal});
  await new Promise(resolve=>setImmediate(resolve));controller.abort();
  await assert.rejects(pending,error=>error.cancelled);
  finish({video:{id:456},request:{files:{}}});
  web.getURL=()=>source.replace('/123/','/999/');
  await assert.rejects(collectVimeo(web,source,entries),error=>error.cancelled);
});
