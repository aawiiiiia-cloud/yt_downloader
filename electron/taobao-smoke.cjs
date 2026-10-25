'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { app } = require('electron');

function fixture(id) {
  const data = {loaderData:{home:{data:{item:{itemId:id,title:`受控商品 ${id}`,images:[
    'https://img.alicdn.com/main1.jpg_800x800q90.jpg','https://img.alicdn.com/main2.jpg']},
    skuBase:{props:[{name:'颜色',values:[{name:'红色',image:'https://img.alicdn.com/red.jpg_80x80.jpg'},{name:'蓝色',image:'https://img.alicdn.com/blue.jpg_80x80.jpg'},{name:'无图规格'}]}]},
    account:{token:'fixture-taobao-account-secret'},video:{url:'https://cloud.video.taobao.com/do-not-collect.mp4'}}}}};
  const state=id==='1070500473161'?'':`<script>window.${id==='784122440183'?'__INITIAL_STATE__':'__ICE_APP_CONTEXT__'}=${JSON.stringify(data)}</script>`;
  return `<html><head><title>受控商品 ${id}</title></head><body><h1>受控商品 ${id}</h1>${state}
    <div class="PicGallery--fixture"><img src="https://img.alicdn.com/main1.jpg_800x800q90.jpg"><img src="https://img.alicdn.com/main2.jpg"><div class="Recommend"><img src="https://img.alicdn.com/recommend.jpg"></div></div>
    <div data-sku-group><span data-vid="1" title="颜色：红色"><img width="50" height="50" src="https://img.alicdn.com/red.jpg_80x80.jpg"></span><span data-vid="2" title="颜色：蓝色"><img width="50" height="50" src="https://img.alicdn.com/blue.jpg_80x80.jpg"></span></div>
    <div id="imageTextInfo-content"><img data-src="//img.alicdn.com/detail1.jpg" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="><img src="https://img.alicdn.com/detail2.gif"></div>
    <div class="review"><img src="https://img.alicdn.com/review.jpg"></div><img src="https://img.alicdn.com/unrelated.jpg"></body></html>`;
}

async function run(collector, check, until, wait, assetRequests) {
  const source=collector.tabs.active, ui=code=>collector.window.webContents.executeJavaScript(code);
  const samples=[['detail.tmall.com','1093500428611','6312145842345'],['chaoshi.detail.tmall.com','1070500473161','6120992941508'],['item.taobao.com','784122440183','6040731631249']];
  const add=collector.queue.add;let spec,title;
  try {
    await ui("document.getElementById('resources-tab').click()");
    for (const [host,id,sku] of samples) {
      const tab=collector.openTab(`https://${host}/item.htm?id=${id}&skuId=${sku}&spm=fixture`,{group:'taobao'});
      await until(()=>tab.state.site==='taobao'&&!tab.view.webContents.isLoadingMainFrame(),'isolated product fixture did not load');
      await collector.inspect();
      check('isolated product parses into three image categories: '+host,collector.state.result?.items.map(row=>row.category).join(',')==='main,sku,detail');
      check('product snapshot excludes recommendations, reviews, account fields and videos: '+host,
        !/recommend.jpg|review.jpg|unrelated.jpg|fixture-taobao-account-secret|do-not-collect.mp4/.test(JSON.stringify(collector.publicState())));
      await until(()=>ui("document.querySelectorAll('[data-image-category]').length===3 && document.querySelector('[data-preview-image]')?.naturalWidth>0"),'product categories or previews did not render');
      check('thumbnail dimensions do not masquerade as original dimensions: '+host,await ui("document.querySelector('.image-meta').textContent.includes('尺寸未知')"));
      await ui("document.querySelector('[data-image=\"1\"]').checked=false;document.querySelector('[data-image-category=\"1\"]').click()");
      await until(()=>ui("document.querySelector('[data-image-name]')?.textContent.includes('颜色：红色')"),'SKU names did not render');
      check('small SKU images are retained with their actual labels: '+host,collector.state.result.items[1].images.length===2&&await ui("document.querySelectorAll('[data-download-image]').length===2"));
      check('product previews use their actual source host as referrer: '+host,assetRequests.some(row=>row.url.includes('img.alicdn.com')&&row.referer?.startsWith('https://'+host+'/')));
      if(host==='detail.tmall.com') {
        await ui("document.querySelector('.image-categories').scrollIntoView({block:'start'})");await wait(120);
        const bounds=await ui("(()=>{const r=document.querySelector('aside').getBoundingClientRect();return{x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)}})()");
        fs.writeFileSync(path.join(app.getPath('userData'),'..','taobao-sku-panel.png'),(await collector.window.webContents.capturePage(bounds)).toPNG());
      }
      collector.queue.add=(value,label)=>{spec=value;title=label;return 'isolated-sku-task';};
      await ui("document.querySelector('[data-download-image=\"2\"]').click()");
      await until(()=>spec?.url.includes(id)&&spec.mediaId==='sku'&&spec.indices[0]===2,'single SKU download did not reach the queue');
      check('single SKU download freezes its category and original product: '+host,spec.context.itemId===id&&spec.indices.length===1&&title.includes('SKU 图')&&title.endsWith('图片 02'));
      await until(()=>ui("!document.querySelector('[data-download-image=\"2\"]').disabled"),'single-image control remained busy');
      await ui("document.querySelector('[data-image-category=\"2\"]').click();document.getElementById('download').click()");
      await until(()=>spec?.mediaId==='detail'&&ui("!document.getElementById('tasks-panel').hidden"),'detail batch did not reach the queue');
      check('detail batch contains only that category and retains source host/SKU: '+host,spec.indices.join(',')==='1,2'&&spec.url.includes(host)&&spec.url.includes('skuId='+sku));
      await ui("document.getElementById('resources-tab').click();document.querySelector('[data-image-category=\"0\"]').click()");
      check('category round trips preserve independent image selections: '+host,await ui("!document.querySelector('[data-image=\"1\"]').checked && document.querySelector('[data-image=\"2\"]').checked"));
      collector.queue.add=add;
    }
    const receipt=collector.state.inspectionId, tab=collector.tabs.active;
    await tab.view.webContents.mainFrame.executeJavaScript("history.pushState({},'', '/item.htm?id=999999&skuId=7')");await wait(150);
    check('manual mode retains its previous product across a product navigation',collector.state.inspectionId===receipt&&collector.state.result.items[0].url.includes('784122440183'));
    // A stale site store must be rejected after an explicit reparse, not attached to a new item.
    const stale=await collector.readPage(require('./taobao-page.cjs').snapshotScript);
    check('a stale product store cannot be relabelled with the newly visited item ID',stale.mismatch===true&&Object.values(stale.groups).every(rows=>!rows.length));
    await tab.view.webContents.mainFrame.executeJavaScript("history.replaceState({},'', '/item.htm?id=784122440183&skuId=6040731631249');document.getElementById('imageTextInfo-content').remove()");
    await collector.inspect();
    check('unloaded details produce an explicit message rather than a false complete collection',collector.state.result?.warnings[0].includes('详情图')&&collector.state.result.items.length===2);
    await ui("document.getElementById('resources-tab').click();document.getElementById('resources-panel').scrollTop=0");await wait(150);
    const bounds=await ui("(()=>{const r=document.querySelector('aside').getBoundingClientRect();return{x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)}})()");
    fs.writeFileSync(path.join(app.getPath('userData'),'..','taobao-panel.png'),(await collector.window.webContents.capturePage(bounds)).toPNG());
    await assert.rejects(()=>collector.download({inspectionId:receipt,itemIndex:0,indices:[1]}),/页面已切换/);
    check('replaced commerce snapshots can no longer queue a download',true);
  } finally { collector.queue.add=add;collector.activateTab(source.id); }
}
module.exports={fixture,run};
