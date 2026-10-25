'use strict';
const { contentKey } = require('./model.cjs');

function externalUrl(value) {
  try {
    const url = new URL(String(value).replace(/&amp;/g, '&'));
    if (url.protocol !== 'https:' || url.username || url.password || url.port) return null;
    if (['www.youtube.com','youtube.com','www.youtube-nocookie.com','youtube-nocookie.com','youtu.be'].includes(url.hostname)) {
      const id = url.hostname === 'youtu.be' ? url.pathname.slice(1) : url.pathname.match(/^\/embed\/([\w-]{11})\/?$/)?.[1] || url.searchParams.get('v');
      if (/^[\w-]{11}$/.test(id || '')) return { provider:'youtube', id, url:'https://www.youtube.com/watch?v='+id };
    }
    if (['player.vimeo.com','vimeo.com','www.vimeo.com'].includes(url.hostname)) {
      const match=url.pathname.match(/^\/(?:video\/)?(\d+)(?:\/([\da-f]+))?\/?$/i);
      const hash=match?.[2] || url.searchParams.get('h') || '';
      if (match && (!hash || /^[\da-f]{1,64}$/i.test(hash)))
        return { provider:'vimeo', id:match[1], url:`https://player.vimeo.com/video/${match[1]}${hash?'?h='+hash:''}` };
    }
  } catch {}
  return null;
}
function externalEntries(context) {
  if (!context?.project || String(context.project.id)!==String(context.projectId)) return [];
  return (context.project.modules || []).slice(0,300).flatMap((module,index)=>{
    if (!['EmbedModule','VideoModule'].includes(module.__typename)) return [];
    const embed=module.originalEmbed || module.fluidEmbed || module.embed || '';
    const value=String(embed).match(/\bsrc=["']([^"']+)/i)?.[1], entry=externalUrl(value);
    if (!entry) return [];
    return [{...entry,key:`${String(module.id || 'module').slice(0,80)}-${index}`}];
  });
}
// Only media fields leave the embedded player. No account/session data is read.
const vimeoConfigScript=String.raw`(()=>{
  const c=typeof playerConfig!=='undefined'?playerConfig:window.playerConfig;
  if(!c?.video)return null;
  const v=c.video, files=v.files || c.request?.files;
  const result={video:{id:v.id,title:v.title,duration:v.duration,thumbs:v.thumbs,
    thumbnail:v.thumbnail,thumbnail_url:v.thumbnail_url,owner:{name:v.owner?.name,url:v.owner?.url}},request:{files,text_tracks:c.request?.text_tracks}};
  const text=JSON.stringify(result);return text.length<=1000000?JSON.parse(text):null;
})()`;
function cancelled() { return Object.assign(new Error('解析已取消或页面已切换'),{cancelled:true}); }
async function bounded(promise,signal,ms=1500) {
  let timer,abort;
  if(signal?.aborted)throw cancelled();
  try { return await Promise.race([promise,new Promise((_,reject)=>{
    timer=setTimeout(()=>reject(new Error('播放器正在加载')),ms);
    abort=()=>reject(cancelled());signal?.addEventListener('abort',abort,{once:true});
  })]); } finally {clearTimeout(timer);signal?.removeEventListener('abort',abort);}
}
async function collectVimeo(web,sourceUrl,entries,{signal,timeout=30000,refresh=false,retryUnready=false,onProgress=()=>{}}={}) {
  const expected=contentKey(sourceUrl), found=new Map();
  const check=()=>{if(signal?.aborted || web.isDestroyed() || contentKey(web.getURL())!==expected)throw cancelled();};
  check();
  const targets=entries.filter(row=>row.provider==='vimeo').slice(0,24);
  if(!targets.length)return found;
  const throttling=web.getBackgroundThrottling?.();web.setBackgroundThrottling?.(false);
  try {
    // Eager loading uses the actual iframe URL already declared by the website.
    // It does not scroll, play media, inject new frames or guess access parameters.
    const prepare=()=>bounded(web.mainFrame.executeJavaScript(`(()=>{const ids=${JSON.stringify(targets.map(row=>row.id))};let count=0;for(const frame of document.querySelectorAll('iframe')){try{const u=new URL(frame.src);if(u.hostname==='player.vimeo.com'&&ids.includes(u.pathname.split('/').pop())){frame.loading='eager';count++;}}catch{}}return count;})()`),signal).catch(error=>{if(error.cancelled)throw error;return 0;});
    await prepare();
    if(refresh)for(const frame of web.mainFrame.framesInSubtree){
      const entry=externalUrl(frame.url);
      if(entry&&targets.some(row=>row.url===entry.url))await bounded(frame.executeJavaScript('location.reload()'),signal).catch(error=>{if(error.cancelled)throw error;});
    }
    const started=Date.now(),deadline=started+timeout,reloaded=new Set();let previous=-1,totalBytes=0;
    do {
      check();
      const present=await prepare();
      await Promise.all(targets.filter(row=>!found.has(row.key)).map(async row=>{
        const frame=web.mainFrame.framesInSubtree.find(frame=>externalUrl(frame.url)?.url===row.url);
        if(!frame)return;
        try {
          const config=await bounded(frame.executeJavaScript(vimeoConfigScript),signal);
          check();
          const bytes=config?JSON.stringify(config).length:0;
          if(String(config?.video?.id)===row.id && config.request?.files && totalBytes+bytes<=8000000){
            totalBytes+=bytes;found.set(row.key,{videoId:row.id,config,capturedAt:Date.now()});
          }
          if(!found.has(row.key)&&retryUnready&&!refresh&&!reloaded.has(row.url)&&Date.now()-started>=Math.min(8000,timeout/2)){
            const blocked=await bounded(frame.executeJavaScript(`/(?:couldn.t verify the security|access to this content has been restricted|此视频不存在|this video does not exist)/i.test(document.body.innerText.slice(0,1000))`),signal);
            if(blocked){reloaded.add(row.url);await bounded(frame.executeJavaScript('location.reload()'),signal).catch(error=>{if(error.cancelled)throw error;});}
          }
        }catch(error){if(error.cancelled)throw error;}
      }));
      if(found.size!==previous){previous=found.size;onProgress(found.size,targets.length,present);}
      if(found.size===targets.length || Date.now()>=deadline)break;
      await bounded(new Promise(resolve=>setTimeout(resolve,350)),signal,1000);
    }while(Date.now()<deadline);
    check();return found;
  }finally{if(!web.isDestroyed()&&typeof throttling==='boolean')web.setBackgroundThrottling?.(throttling);}
}
module.exports={externalUrl,externalEntries,collectVimeo,vimeoConfigScript};
