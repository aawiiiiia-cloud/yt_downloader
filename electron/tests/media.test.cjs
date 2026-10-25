'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { Assets, assetUrl, referrerOrigin, imageDimensions, readDimensions } = require('../assets.cjs');
const { duration, size, videoSize } = require('../ui/media-info.js');
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAE0lEQVR4nGO8/Po+AwwwwVnoHABhQQKjPQkWWQAAAABJRU5ErkJggg==', 'base64');
test('durations preserve seconds and support hours; unknown sizes are not zero', () => {
  assert.equal(duration(3527), '58:47'); assert.equal(duration(7127), '1:58:47');
  assert.equal(duration(0), '00:00'); assert.equal(duration(null), '时长未知');
  assert.equal(size(340000), '0.34 MB'); assert.equal(size(340000, true), '约 0.34 MB'); assert.equal(size(null), '大小未知');
});
test('selected video size includes audio, estimates bitrate, and avoids double counting muxed streams', () => {
  const item = { duration: 100, formats: [
    { id: 'mux', video: true, audio: true, size: 2000000 },
    { id: 'audio', video: false, audio: true, size: 1000000 },
    { id: 'hd', video: true, audio: false, size: 10000000 },
    { id: 'estimate', video: true, audio: false, bitrate: 800 },
  ] };
  assert.equal(videoSize(item, 'hd').size, 11000000);
  assert.equal(videoSize(item, 'mux').size, 2000000);
  assert.deepEqual(videoSize(item, 'estimate'), { size: 11000000, estimated: true });
  assert.deepEqual(videoSize(item, '', true), { size: 2400000, estimated: true });
  assert.equal(videoSize({ formats: [{ id: 'x', video: true }] }, 'x').size, null);
});
test('trusted HTTP covers are upgraded; local, credentialed and spoofed resources are rejected', () => {
  assert.equal(assetUrl('http://i0.hdslb.com/cover.jpg', 'bilibili'), 'https://i0.hdslb.com/cover.jpg');
  assert.equal(assetUrl('//i.ytimg.com/cover.jpg', 'youtube'), 'https://i.ytimg.com/cover.jpg');
  for (const value of ['http://127.0.0.1/private', 'https://hdslb.com.evil.invalid/a', 'https://u:p@i0.hdslb.com/a']) assert.throws(() => assetUrl(value, 'bilibili'));
});
test('cover requests use the site referer and cache bytes; HTML cannot be shown as an image', async () => {
  let calls = 0;
  const assets = new Assets(async (url, options) => {
    calls++; assert.equal(url, 'https://i0.hdslb.com/cover.jpg'); assert.equal(options.headers.Referer, 'https://www.bilibili.com/');
    return new Response(png);
  });
  const preview = await assets.preview('http://i0.hdslb.com/cover.jpg', 'bilibili');
  assert.match(preview.dataUrl, /^data:image\/png;base64,/);
  await assets.preview('http://i0.hdslb.com/cover.jpg', 'bilibili'); assert.equal(calls, 1);
  const invalid = new Assets(async () => new Response('<html>blocked</html>', { headers: { 'content-type': 'image/jpeg' } }));
  await assert.rejects(() => invalid.preview('https://i0.hdslb.com/a', 'bilibili'), /没有返回/);
});
test('range metadata uses full resource size and cancels the response without downloading it', async () => {
  const methods = [];
  const assets = new Assets(async (_url, options) => {
    methods.push(options.method);
    if (options.method === 'HEAD') return new Response(null, { status: 405 });
    assert.equal(options.headers.Range, 'bytes=0-0');
    return new Response(png, { status: 206, headers: { 'content-range': 'bytes 0-0/340000', 'content-length': '1', 'content-type': 'image/jpeg' } });
  });
  assert.equal((await assets.sourceSize(['https://sns-na-i11.xhscdn.com/a'], 'xiaohongshu')).size, 340000);
  assert.deepEqual(methods, ['HEAD', 'GET']);
});
test('redirects cannot turn cover or metadata fetching into local network requests', async () => {
  let requests = 0;
  const assets = new Assets(async () => { requests++; return new Response(null, { status: 302, headers: { location: 'http://127.0.0.1/private' } }); });
  await assert.rejects(() => assets.preview('https://i0.hdslb.com/a', 'bilibili'), /受支持/);
  assert.equal(requests, 1);
});

test('cross-origin image requests use an origin-only referrer under the default security policy', async () => {
  assert.equal(referrerOrigin('https://detail.tmall.com/item.htm?id=123&skuId=456#tracking'),'https://detail.tmall.com/');
  for(const value of ['file:///C:/private','https://user:pass@detail.tmall.com/']) assert.throws(()=>referrerOrigin(value));
  const assets=new Assets(async(_url,options)=>{
    assert.equal(options.headers.Referer,'https://detail.tmall.com/');assert.equal(options.referrerPolicy,undefined);
    return new Response(png);
  });
  await assets.preview('https://img.alicdn.com/a.jpg','taobao',undefined,false,'https://detail.tmall.com/item.htm?id=123&skuId=456');
});

test('source header parsing supports PNG, GIF, JPEG and WebP without using layout dimensions', () => {
  assert.deepEqual(imageDimensions(png),{width:4,height:3});
  const gif=Buffer.alloc(10);gif.write('GIF89a');gif.writeUInt16LE(1920,6);gif.writeUInt16LE(1080,8);
  assert.deepEqual(imageDimensions(gif),{width:1920,height:1080});
  const jpeg=Buffer.from([0xff,0xd8,0xff,0xe1,0,4,0,0,0xff,0xc2,0,8,8,4,0x38,7,0x80,3]);
  assert.deepEqual(imageDimensions(jpeg),{width:1920,height:1080});
  assert.equal(imageDimensions(jpeg.subarray(0,15)),null);
  const webp=Buffer.alloc(30);webp.write('RIFF');webp.write('WEBP',8);webp.write('VP8X',12);webp.writeUIntLE(1919,24,3);webp.writeUIntLE(1079,27,3);
  assert.deepEqual(imageDimensions(webp),{width:1920,height:1080});
  webp.write('VP8L',12);webp[20]=0x2f;webp[21]=0x7f;webp[22]=0xc7;webp[23]=0x0d;webp[24]=1;
  assert.deepEqual(imageDimensions(webp),{width:1920,height:1080});
  webp.write('VP8 ',12);webp[23]=0x9d;webp[24]=1;webp[25]=0x2a;webp.writeUInt16LE(1920,26);webp.writeUInt16LE(1080,28);
  assert.deepEqual(imageDimensions(webp),{width:1920,height:1080});
  for(const bytes of [Buffer.alloc(0),Buffer.from('<html>denied</html>'),Buffer.from([0xff,0xd8,0xff,0xc0,0,0])])assert.equal(imageDimensions(bytes),null);
});

test('dimension probing cancels a stream as soon as the source header is available', async () => {
  let cancelled=false;
  const response=new Response(new ReadableStream({start(controller){controller.enqueue(png);},cancel(){cancelled=true;}}));
  assert.deepEqual(await readDimensions(response),{width:4,height:3});assert.equal(cancelled,true);
});

test('unknown headers have a strict bounded read and never invent pixel dimensions', async () => {
  let reads=0,cancelled=false;
  const response=new Response(new ReadableStream({pull(controller){reads++;controller.enqueue(new Uint8Array(65536));},cancel(){cancelled=true;}}));
  assert.deepEqual(await readDimensions(response),{});assert.ok(reads<=5);assert.ok(cancelled);
});

test('source size and dimensions are retrieved together from a bounded range, even if range is ignored', async () => {
  const methods=[];
  const assets=new Assets(async(_url,options)=>{
    methods.push(options.method);
    if(options.method==='HEAD')return new Response(null,{headers:{'content-length':'340000','content-type':'image/png'}});
    assert.equal(options.headers.Range,'bytes=0-262143');
    return new Response(png,{headers:{'content-length':'340000','content-type':'image/png'}});
  });
  const result=await assets.sourceSize(['https://img.alicdn.com/a.png'],'taobao',undefined,'https://detail.tmall.com/item.htm?id=123',true);
  assert.deepEqual(result,{size:340000,width:4,height:3,displayFallback:false});assert.deepEqual(methods,['HEAD','GET']);
});

test('missing length can still return actual dimensions; HEAD metadata survives a failed dimension request', async () => {
  const noLength=new Assets(async(_url,options)=>options.method==='HEAD'?new Response(null,{status:405}):new Response(png,{headers:{'content-type':'image/png'}}));
  const result=await noLength.sourceSize(['https://img.alicdn.com/a.png'],'taobao',undefined,undefined,true);
  assert.equal(result.size,null);assert.equal(result.width,4);
  const failedGet=new Assets(async(_url,options)=>{
    if(options.method==='GET')throw new Error('connection reset');
    return new Response(null,{headers:{'content-type':'image/png','content-length':'340000'}});
  });
  assert.equal((await failedGet.sourceSize(['https://img.alicdn.com/a.png'],'taobao',undefined,undefined,true)).size,340000);
});

test('fallback image sizes cannot be mistaken for original pixel dimensions', async () => {
  const calls=[];
  const assets=new Assets(async(url,options)=>{
    calls.push([url,options.method]);
    if(url.includes('original'))return new Response(null,{status:403});
    return new Response(null,{headers:{'content-type':'image/png','content-length':'340000'}});
  });
  const result=await assets.sourceSize(['https://img.alicdn.com/original.png','https://img.alicdn.com/display.png'],'taobao',undefined,undefined,true);
  assert.equal(result.displayFallback,true);assert.equal(result.width,undefined);
  assert.equal(calls.filter(([url,method])=>url.includes('display')&&method==='GET').length,0);
});

test('a dimension timeout keeps known bytes, while a page cancellation discards the request', async t => {
  const deadline=new AbortController();t.mock.method(AbortSignal,'timeout',()=>deadline.signal);
  const assets=new Assets(async(_url,options)=>{
    if(options.method==='HEAD')return new Response(null,{headers:{'content-type':'image/png','content-length':'340000'}});
    deadline.abort(new DOMException('Timed out','TimeoutError'));throw deadline.signal.reason;
  });
  assert.equal((await assets.sourceSize(['https://img.alicdn.com/a.png'],'taobao',undefined,undefined,true)).size,340000);
  const freshDeadline=new AbortController();t.mock.method(AbortSignal,'timeout',()=>freshDeadline.signal);
  const page=new AbortController();
  const cancelled=new Assets(async(_url,options)=>{
    if(options.method==='HEAD')return new Response(null,{headers:{'content-type':'image/png','content-length':'340000'}});
    page.abort();throw page.signal.reason;
  });
  await assert.rejects(()=>cancelled.sourceSize(['https://img.alicdn.com/a.png'],'taobao',page.signal,undefined,true),{name:'AbortError'});
});
