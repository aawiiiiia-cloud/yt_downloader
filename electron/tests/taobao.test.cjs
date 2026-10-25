'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { productMedia, snapshotScript } = require('../taobao-page.cjs');
const { contentKey, siteForUrl, hasAuth, cookieText } = require('../model.cjs');
const { SiteTabs } = require('../site-tabs.cjs');
const { Assets, assetUrl } = require('../assets.cjs');
const { fingerprint } = require('../youtube-auth.cjs');

test('all three product hosts share a group and item identity while SKU and tracking vary', () => {
  for (const host of ['item.taobao.com','detail.tmall.com','chaoshi.detail.tmall.com']) {
    const url = `https://${host}/item.htm?id=784122440183&skuId=1&spm=tracking`;
    assert.equal(siteForUrl(url),'taobao'); assert.equal(contentKey(url),'taobao:784122440183');
  }
  for (const url of ['https://tmall.com.evil.invalid/item.htm?id=1','https://www.taobao.com/?id=1','https://detail.tmall.com/item.htm?id=not-an-id']) assert.equal(contentKey(url),null);
  const tabs=new SiteTabs(), a=tabs.add('taobao','https://detail.tmall.com/item.htm?id=123&skuId=1');
  assert.equal(tabs.find('https://detail.tmall.com/item.htm?id=123&skuId=2'),a);
  assert.equal(tabs.switchGroup('taobao'),a);
});

test('product JSON extracts only media for the matching item, retaining SKU names', () => {
  const root={data:{item:{itemId:'123',title:'商品',images:['https://img.alicdn.com/a.jpg']},skuBase:{props:[{name:'颜色',values:[{name:'红',image:'https://img.alicdn.com/red.jpg'},{name:'无图'}]}]},
    account:{token:'secret'},video:{url:'https://cloud.video.taobao.com/video.mp4'}}};
  const result=productMedia(root,'123');
  assert.equal(result.main.length,1);assert.equal(result.sku[0].name,'颜色：红');
  assert.ok(!JSON.stringify(result).includes('secret'));assert.ok(!JSON.stringify(result).includes('.mp4'));
  assert.equal(productMedia(root,'456').main.length,0);assert.equal(productMedia(root,'456').other,true);
});

test('unrelated recommendations and malformed props cannot contaminate product extraction', () => {
  const root={recommendations:{item:{itemId:'456',images:['https://img.alicdn.com/wrong.jpg']}},data:{item:{itemId:'123',images:[]},skuBase:{props:{invalid:true}},detailImages:{invalid:true}}};
  const result=productMedia(root,'123');assert.equal(result.matched,true);assert.equal(result.other,false);assert.equal(result.main.length,0);
  root.circular=root;assert.doesNotThrow(()=>productMedia(root,'999'));new Function(snapshotScript);
  root.data.skuBase.props=[null,{values:[null,'invalid']}];assert.doesNotThrow(()=>productMedia(root,'123'));
});

test('Taobao CDN previews are allowlisted and upgraded without dropping signed parameters', () => {
  assert.equal(assetUrl('http://img.alicdn.com/a.jpg?signature=keep','taobao'),'https://img.alicdn.com/a.jpg?signature=keep');
  for (const url of ['https://alicdn.com.evil.invalid/a.jpg','https://127.0.0.1/a.jpg','file:///C:/private.png','https://user:pass@img.alicdn.com/a.jpg']) assert.throws(()=>assetUrl(url,'taobao'));
});

test('Tmall source-size requests retain their product referrer', async () => {
  const referrer='https://chaoshi.detail.tmall.com/item.htm?id=123&skuId=7';
  const assets=new Assets(async(_url,options)=>{
    assert.equal(options.headers.Referer,referrer);
    return new Response(null,{headers:{'content-type':'image/jpeg','content-length':'340000'}});
  });
  assert.equal((await assets.sourceSize(['https://img.alicdn.com/a.jpg'],'taobao',undefined,referrer)).size,340000);
});

test('Taobao and Tmall credentials stay in one group while anonymous tokens are ignored', () => {
  const anon={domain:'.taobao.com',name:'_tb_token_',value:'anon'};
  assert.equal(hasAuth([anon],'taobao'),false);
  const tmall={domain:'.tmall.com',name:'sgcookie',value:'credential',path:'/',secure:true};
  assert.equal(hasAuth([tmall],'taobao'),true);assert.ok(fingerprint([tmall],'taobao'));
  assert.notEqual(fingerprint([tmall],'taobao'),fingerprint([{...tmall,value:'new'}],'taobao'));
  assert.equal(hasAuth([tmall],'youtube'),false);
  assert.ok(!cookieText([tmall,{domain:'.youtube.com',name:'SID',value:'foreign'}],'taobao').includes('foreign'));
});
