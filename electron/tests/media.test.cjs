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

test('source size and dimensions use one bounded range, even if range is ignored', async () => {
  const methods=[];
  const assets=new Assets(async(_url,options)=>{
    methods.push(options.method);
    if(options.method==='HEAD')return new Response(null,{headers:{'content-length':'340000','content-type':'image/png'}});
    assert.equal(options.headers.Range,'bytes=0-262143');
    return new Response(png,{headers:{'content-length':'340000','content-type':'image/png'}});
  });
  const result=await assets.sourceSize(['https://img.alicdn.com/a.png'],'taobao',undefined,'https://detail.tmall.com/item.htm?id=123',true);
  assert.deepEqual(result,{size:340000,width:4,height:3,displayFallback:false});assert.deepEqual(methods,['GET']);
});

test('missing length can still return actual dimensions; HEAD is a fallback after a failed dimension request', async () => {
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
  let deadline=new AbortController();t.mock.method(AbortSignal,'timeout',()=>deadline.signal);
  const assets=new Assets(async()=>new Response(new ReadableStream({pull(controller){
    deadline.abort(new DOMException('Timed out','TimeoutError'));controller.error(deadline.signal.reason);
  }}),{headers:{'content-type':'image/png','content-length':'340000'}}));
  assert.equal((await assets.sourceSize(['https://img.alicdn.com/a.png'],'taobao',undefined,undefined,true)).size,340000);
  deadline=new AbortController();
  const page=new AbortController();
  const cancelled=new Assets(async(_url,options)=>{
    page.abort();throw page.signal.reason;
  });
  await assert.rejects(()=>cancelled.sourceSize(['https://img.alicdn.com/a.png'],'taobao',page.signal,undefined,true),{name:'AbortError'});
});

test('a CDN range reports the actual variant length without trusting an original-file HEAD length', async () => {
  const methods=[];
  const assets=new Assets(async(_url,options)=>{
    methods.push(options.method);
    if(options.method==='HEAD')throw new Error('HEAD must not spend the dimension deadline');
    return new Response(png,{status:206,headers:{'content-type':'image/png','content-length':String(png.length),'content-range':`bytes 0-${png.length-1}/311246`}});
  });
  assert.deepEqual(await assets.sourceSize(['https://img.zcool.cn/community/image.jpg'],'zcool',undefined,undefined,true),
    {size:311246,width:4,height:3,displayFallback:false});
  assert.deepEqual(methods,['GET']);
});

test('incomplete dimensions expire quickly and explicit retry reloads both metadata caches', async t => {
  let now=100000,calls=0;t.mock.method(Date,'now',()=>now);
  const assets=new Assets(async(_url,options)=>{
    calls++; assert.equal(options.method,'GET');
    assert.equal(options.cache,calls===3?'reload':'default');
    return new Response(calls===1?Buffer.alloc(1):png,{headers:{'content-type':'image/png','content-length':'2093605'}});
  });
  const size=refresh=>assets.sourceSize(['https://img.zcool.cn/community/image.jpg'],'zcool',undefined,undefined,true,refresh);
  assert.equal((await size()).width,undefined);assert.equal(calls,1);
  now+=4000;assert.equal((await size()).width,undefined);assert.equal(calls,1);
  now+=1001;assert.equal((await size()).width,4);assert.equal(calls,2);
  now+=10000;assert.equal((await size()).width,4);assert.equal(calls,2);
  assert.equal((await size(true)).height,3);assert.equal(calls,3);
});

test('a large JPEG EXIF header split across network chunks still yields the real pixel dimensions', async () => {
  // Like the reported 01.jpg: its SOF marker follows roughly 54 KB of metadata.
  const exif=Buffer.alloc(54019);exif[0]=0xff;exif[1]=0xe1;exif.writeUInt16BE(54017,2);
  const header=Buffer.concat([Buffer.from([0xff,0xd8]),exif,Buffer.from([0xff,0xc0,0,17,8,0x0f,0x75,0x0b,0x98,3,1,0x11,0,2,0x11,0,3,0x11,0])]);
  let offset=0,cancelled=false;
  const response=new Response(new ReadableStream({pull(controller){
    if(offset>=header.length){controller.close();return;}
    controller.enqueue(header.subarray(offset,offset+1024));offset+=1024;
  },cancel(){cancelled=true;}}));
  assert.equal(imageDimensions(header.subarray(0,32768)),null);
  assert.deepEqual(await readDimensions(response),{width:2968,height:3957});assert.ok(cancelled);
});

test('slow original metadata leaves preview capacity without increasing the four-request limit', async () => {
  const gate = Promise.withResolvers();
  let active = 0, maximum = 0, metadataActive = 0, metadataMaximum = 0;
  const assets = new Assets(async (_url, options) => {
    const metadata = options.method === 'HEAD';
    maximum = Math.max(maximum, ++active);
    if (metadata) { metadataMaximum = Math.max(metadataMaximum, ++metadataActive); await gate.promise; metadataActive--; }
    active--;
    return new Response(metadata ? null : png, { headers: { 'content-type': 'image/png', 'content-length': String(png.length) } });
  });
  const sizes = Array.from({ length: 6 }, (_, i) => assets.sourceSize([`https://img.zcool.cn/source-${i}.png`], 'zcool'));
  try {
    await Promise.all(Array.from({ length: 6 }, (_, i) => assets.preview(`https://img.zcool.cn/preview-${i}.png`, 'zcool')));
    assert.equal(metadataActive, 2, 'previews finish while the original requests are still blocked');
    assert.ok(maximum <= 4); assert.equal(metadataMaximum, 2);
  } finally { gate.resolve(); await Promise.all(sizes); }
  assert.equal(assets.active, 0); assert.equal(assets.queue.length, 0);
});

test('a newly queued preview starts before older queued metadata after a slot opens', async () => {
  const gates = Array.from({ length: 4 }, () => Promise.withResolvers()), calls = [];
  const assets = new Assets(async (url, options) => {
    const image = Number(new URL(url).pathname.match(/\d+/)[0]);
    const metadata = options.method === 'HEAD'; calls.push(metadata ? 'metadata' : `preview-${image}`);
    if (!metadata && image < 4) await gates[image].promise;
    return new Response(metadata ? null : png, { headers: { 'content-type': 'image/png', 'content-length': String(png.length) } });
  });
  const initial = gates.map((_, i) => assets.preview(`https://img.zcool.cn/${i}.png`, 'zcool'));
  const size = assets.sourceSize(['https://img.zcool.cn/5.png'], 'zcool');
  const latest = assets.preview('https://img.zcool.cn/6.png', 'zcool');
  try {
    gates[0].resolve(); await latest;
    assert.ok(calls.indexOf('preview-6') < calls.indexOf('metadata') || !calls.includes('metadata'));
  } finally { gates.forEach(gate => gate.resolve()); await Promise.all([...initial, size]); }
  assert.ok(calls.includes('metadata'), 'metadata also completes after the previews');
});

test('cancelled queued assets settle immediately and never issue stale network requests', async () => {
  const gate = Promise.withResolvers(), calls = [];
  const assets = new Assets(async url => { calls.push(url); await gate.promise; return new Response(png); });
  const running = Array.from({ length: 4 }, (_, i) => assets.preview(`https://img.zcool.cn/${i}.png`, 'zcool'));
  const page = new AbortController();
  const stalePreview = assets.preview('https://img.zcool.cn/stale-preview.png', 'zcool', page.signal);
  const staleSize = assets.sourceSize(['https://img.zcool.cn/stale-size.png'], 'zcool', page.signal);
  const rejected = [assert.rejects(stalePreview, /页面已切换/), assert.rejects(staleSize, /页面已切换/)];
  try {
    page.abort(); await Promise.all(rejected);
    assert.equal(assets.queue.length, 0); assert.equal(assets.active, 4);
  } finally { gate.resolve(); await Promise.all(running); }
  assert.equal(calls.length, 4); assert.equal(assets.active, 0);
  assert.ok(calls.every(url => !url.includes('stale')));
});

test('an original preview and its metadata share one request, including a fresh retry', async () => {
  const calls = [], assets = new Assets(async (_url, options) => {
    calls.push(options); return new Response(png, { headers: { 'content-type': 'image/png', 'content-length': String(png.length) } });
  });
  const url = 'https://img.alicdn.com/original.png', page = new AbortController();
  for (const refresh of [false, true]) {
    const before = calls.length;
    const [preview, metadata] = await Promise.all([
      assets.preview(url, 'taobao', page.signal, refresh), assets.sourceSize([url], 'taobao', page.signal, undefined, true, refresh),
    ]);
    assert.match(preview.dataUrl, /^data:image\/png/);
    assert.deepEqual(metadata, { size: png.length, width: 4, height: 3, displayFallback: false });
    assert.equal(calls.length, before + 1); assert.equal(calls.at(-1).cache, refresh ? 'reload' : 'default');
    assert.equal(calls.at(-1).headers.Range, undefined);
  }
  assert.equal(assets.previewRequests.size, 0);
  await assets.sourceSize([url], 'taobao', page.signal, undefined, true); assert.equal(calls.length, 2);
});

test('original metadata is available from the header before a slow preview body finishes', async () => {
  let stream, finished = false, calls = 0;
  const assets = new Assets(async () => {
    calls++; return new Response(new ReadableStream({ start(controller) { stream = controller; controller.enqueue(png.subarray(0, 24)); } }),
      { headers: { 'content-type': 'image/png', 'content-length': String(png.length) } });
  });
  const url = 'https://img.alicdn.com/original.png';
  const preview = assets.preview(url, 'taobao').then(result => { finished = true; return result; });
  try {
    const metadata = await assets.sourceSize([url], 'taobao', undefined, undefined, true);
    assert.equal(metadata.size, png.length); assert.equal(metadata.width, 4); assert.equal(finished, false); assert.equal(calls, 1);
  } finally { stream.enqueue(png.subarray(24)); stream.close(); await preview; }
});

test('thumbnail pixels cannot supply original dimensions or bytes', async () => {
  const calls = [], original = Buffer.from(png); original.writeUInt32BE(1600, 16); original.writeUInt32BE(1200, 20);
  const assets = new Assets(async (url, options) => {
    calls.push(url); return new Response(url.endsWith('thumb.png') ? png : original,
      { headers: { 'content-type': 'image/png', 'content-length': url.endsWith('thumb.png') ? String(png.length) : '2400000' } });
  });
  const [preview, metadata] = await Promise.all([assets.preview('https://img.alicdn.com/thumb.png', 'taobao'),
    assets.sourceSize(['https://img.alicdn.com/original.png', 'https://img.alicdn.com/thumb.png'], 'taobao', undefined, undefined, true)]);
  assert.match(preview.dataUrl, /^data:image\/png/); assert.equal(metadata.width, 1600); assert.equal(metadata.size, 2400000);
  assert.equal(metadata.displayFallback, false); assert.equal(calls.length, 2);
});

test('pending preview reuse stays in its page signal and referrer; cancellation settles both consumers', async () => {
  const page = new AbortController(); let calls = 0;
  const assets = new Assets(async (_url, options) => {
    calls++; return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true }));
  });
  const url = 'https://img.alicdn.com/original.png';
  const preview = assets.preview(url, 'taobao', page.signal), duplicate = assets.preview(url, 'taobao', page.signal);
  const metadata = assets.sourceSize([url], 'taobao', page.signal, undefined, true);
  const assertions = [assert.rejects(preview), assert.rejects(duplicate), assert.rejects(metadata)];
  await new Promise(setImmediate); page.abort(); await Promise.all(assertions);
  assert.equal(calls, 1); assert.equal(assets.previewRequests.size, 0); assert.equal(assets.active, 0);
  const seen = [], isolated = new Assets(async (_url, options) => { seen.push(options.headers.Referer); return new Response(png,
    { headers: { 'content-type': 'image/png', 'content-length': String(png.length) } }); });
  await Promise.all([isolated.preview(url, 'taobao', undefined, false, 'https://item.taobao.com/item.htm?id=1'),
    isolated.sourceSize([url], 'taobao', undefined, 'https://detail.tmall.com/item.htm?id=2', true)]);
  assert.deepEqual(seen, ['https://item.taobao.com/', 'https://detail.tmall.com/']);
});

test('a stalled original preview cannot hold metadata behind its first byte indefinitely', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const first = Promise.withResolvers(); let calls = 0;
  const reply = () => new Response(png, { headers: { 'content-type': 'image/png', 'content-length': String(png.length) } });
  const assets = new Assets(async () => ++calls === 1 ? first.promise : reply());
  const url = 'https://img.alicdn.com/original.png', preview = assets.preview(url, 'taobao');
  const metadata = assets.sourceSize([url], 'taobao', undefined, undefined, true);
  try {
    await new Promise(setImmediate); assert.equal(calls, 1);
    t.mock.timers.tick(1000);
    assert.equal((await metadata).width, 4); assert.equal(calls, 2);
  } finally { first.resolve(reply()); await preview; }
});
