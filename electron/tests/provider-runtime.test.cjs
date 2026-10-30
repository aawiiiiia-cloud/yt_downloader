'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const path=require('node:path'),readline=require('node:readline');

test('provider preload preserves HTTP behavior and records durations without request or token contents',async t=>{
  const preload=path.resolve(__dirname,'../../provider_runtime.cjs');
  const code=`const http=require('node:http');const server=http.createServer((req,res)=>{
    req.resume();res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify({poToken:'PRIVATE_TOKEN_VALUE'}));
  });server.listen(0,'127.0.0.1',()=>console.log('READY '+server.address().port));`;
  const child=spawn(process.execPath,['--require',preload,'-e',code],{windowsHide:true,stdio:['ignore','pipe','pipe']});
  let output='';const rows=[];child.stdout.on('data',data=>output+=data);
  t.after(()=>child.kill());
  const port=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('provider profiling fixture timeout')),5000);
    const lines=readline.createInterface({input:child.stdout});
    child.once('error',reject);child.once('exit',()=>reject(new Error('profiling fixture exited early')));
    lines.on('line',line=>{
      if(line.startsWith('MEDIA_DL_PROVIDER_TIMING '))rows.push(JSON.parse(line.slice('MEDIA_DL_PROVIDER_TIMING '.length)));
      if(line.startsWith('READY ')){clearTimeout(timer);resolve(Number(line.slice(6)));}
    });
  });
  const response=await fetch(`http://127.0.0.1:${port}/get_pot`,{method:'POST',headers:{Cookie:'PRIVATE_COOKIE_VALUE'},body:'PRIVATE_BINDING_VALUE'});
  assert.equal(response.status,200);assert.equal((await response.json()).poToken,'PRIVATE_TOKEN_VALUE');
  const deadline=Date.now()+2000;
  while(Date.now()<deadline&&!rows.some(row=>row.stage==='token'))await new Promise(resolve=>setTimeout(resolve,10));
  assert.ok(rows.some(row=>row.stage==='node-ready'&&row.seconds>=0));
  assert.ok(rows.some(row=>row.stage==='token'&&row.status===200&&row.seconds>=0));
  assert.doesNotMatch(output,/PRIVATE_TOKEN_VALUE|PRIVATE_COOKIE_VALUE|PRIVATE_BINDING_VALUE/);
});
