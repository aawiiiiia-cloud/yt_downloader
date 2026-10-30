'use strict';
const fs = require('node:fs'), path = require('node:path');
const { shell } = require('electron');
const { DownloadQueue } = require('./model.cjs');

async function run(collector, check, until, wait, profile) {
  const originalQueue=collector.queue, source=collector.tabs.active, opener=shell.openPath;
  const login={...collector.state.login}, expired={...collector.state.loginExpired};
  const settings={...collector.settings}, size=collector.window.getSize(), visible=collector.window.isVisible();
  const ui=code=>collector.window.webContents.executeJavaScript(code), jobs=[];
  let mode='success', shellCalls=0, releaseOpen;
  const queue=new DownloadQueue(()=>{
    const job={cancelled:false};jobs.push(job);
    return{promise:new Promise(resolve=>{job.resolve=resolve;}),cancel(){job.cancelled=true;job.resolve({});}};
  },()=>collector.publish(),{globalConcurrency:1,siteConcurrency:1});
  try {
    collector.queue=queue;collector.window.show();collector.window.setSize(1060,780);
    collector.settings.panelWidth=330;
    await collector.invoke('switch-site',{site:'xiaohongshu'});
    await ui('document.getElementById("resources-tab").click()');
    const xhs=collector.tabs.active;
    const borderScript='(()=>{const card=getComputedStyle(document.querySelector(".login-card")),label=getComputedStyle(document.getElementById("login-state"));return [card.borderTopColor,card.borderRightColor,card.borderBottomColor,card.borderLeftColor].every(color=>color===label.color)})()';
    collector.state.login.xiaohongshu=true;collector.state.loginExpired.xiaohongshu=false;collector.publish();
    const detectedScript='(()=>{const label=document.getElementById("login-state");return label.textContent.includes("已检测到登录凭据")&&!label.classList.contains("login-warning")&&!label.classList.contains("login-success")&&getComputedStyle(document.querySelector(".login-card")).borderTopColor==="rgb(227, 227, 227)"})()';
    await until(()=>ui(detectedScript),'detected credentials label');
    check('detected credentials retain a neutral card without claiming actual website login',await ui(detectedScript));
    collector.state.login.xiaohongshu=false;collector.publish();
    await until(()=>ui('document.getElementById("login-state").classList.contains("login-warning")'),'logged out label');
    check('all four logged-out card borders match the red login label',await ui(borderScript));
    collector.state.login.xiaohongshu=true;collector.state.loginExpired.xiaohongshu=true;collector.publish();
    await until(()=>ui('document.getElementById("login-state").textContent.includes("已检测到登录凭据")&&document.getElementById("login-state").classList.contains("login-warning")'),'rejected credentials label');
    check('expired credentials keep the red account border',await ui(borderScript));
    await wait(100);
    fs.writeFileSync(path.join(profile,'..','login-border-red.png'),(await collector.window.webContents.capturePage()).toPNG());
    collector.state.login.behance=false;collector.state.loginExpired.behance=false;
    await collector.invoke('switch-site',{site:'behance'});
    await until(()=>ui('document.getElementById("login-state").textContent.includes("未检测到登录凭据")'),'Behance credential label');
    check('Behance missing credentials use the common warning without asserting actual website login',await ui(borderScript+'&&document.getElementById("login-state").classList.contains("login-warning")'));
    collector.activateTab(xhs.id);collector.state.loginExpired.xiaohongshu=false;collector.publish();
    await until(()=>ui(detectedScript),'neutral credentials label restored');
    await wait(100);
    fs.writeFileSync(path.join(profile,'..','login-border-detected.png'),(await collector.window.webContents.capturePage()).toPNG());

    const output=path.join(profile,'history-output'),moved=path.join(profile,'history-output-moved');
    fs.mkdirSync(output);fs.writeFileSync(path.join(output,'fixture.png'),'local fixture media');fs.renameSync(output,moved);
    shell.openPath=async()=>{
      shellCalls++;
      if(mode==='pending')return new Promise(resolve=>{releaseOpen=resolve;});
      return mode==='failure'?'controlled OS failure':'';
    };
    const running=queue.add({url:'https://vimeo.com/700001'},'保留运行任务');
    const waiting=queue.add({url:'https://vimeo.com/700002'},'保留排队任务');
    queue.tasks.unshift({id:'history-completed',title:'已移动的本地资源',status:'completed',percent:100,logs:[],result:{folder:output}},
      {id:'history-failed',title:'失败记录',status:'failed',error:'本地测试错误',logs:[]},
      {id:'history-cancelled',title:'取消记录',status:'cancelled',error:'操作已取消',logs:[]});
    collector.publish();await ui('document.getElementById("tasks-tab").click()');
    await until(()=>ui('document.querySelectorAll(".task-card").length===5'),'task records rendered');
    check('clear records is available with terminal history in a narrow task panel',await ui('!document.getElementById("clear-history").disabled&&document.getElementById("clear-history").getBoundingClientRect().right<=document.querySelector("aside").getBoundingClientRect().right'));
    const clickOpen=()=>ui('document.querySelector("[data-folder=history-completed]").click()');
    await clickOpen();
    await until(()=>ui('document.querySelector("[data-task-id=history-completed] .task-folder-error")?.textContent.includes("删除或移动")'),'missing folder card feedback');
    check('a moved directory is reported inside its completed card without calling the OS opener',shellCalls===0&&queue.tasks.find(task=>task.id==='history-completed').status==='completed'&&await ui('document.querySelector("[data-task-id=history-completed] .task-meta").textContent.includes("已完成")'));
    check('folder feedback occupies the left side beside the open-directory button',await ui('(()=>{const e=document.querySelector(".task-folder-error").getBoundingClientRect(),b=document.querySelector("[data-folder=history-completed]").getBoundingClientRect();return e.right<=b.left&&e.left>=document.querySelector(".task-card").getBoundingClientRect().left})()'));
    await wait(120);
    fs.writeFileSync(path.join(profile,'..','task-folder-missing.png'),(await collector.window.webContents.capturePage()).toPNG());
    fs.mkdirSync(output);fs.writeFileSync(path.join(output,'kept.txt'),'do not delete');
    await clickOpen();await until(()=>ui('!document.querySelector("[data-folder=history-completed]").disabled'),'successful folder open');
    check('a successful retry clears the prior directory error',shellCalls===1&&await ui('!document.querySelector("[data-task-id=history-completed] .task-folder-error")'));
    mode='failure';await clickOpen();
    await until(()=>ui('document.querySelector(".task-folder-error")?.textContent.includes("无法打开目录")'),'OS failure feedback');
    check('resolved OS error strings also produce inline card feedback',shellCalls===2);
    mode='pending';await clickOpen();await until(()=>!!releaseOpen,'pending folder open');
    await ui('document.getElementById("clear-history").click()');
    await until(()=>queue.tasks.length===2&&ui('document.querySelectorAll(".task-card").length===2'),'terminal records cleared');
    const saved=JSON.parse(fs.readFileSync(path.join(profile,'history.json'),'utf8'));
    check('clear records persists immediately and keeps only the running and queued jobs',saved.length===2&&saved.some(task=>task.id===running)&&saved.some(task=>task.id===waiting)&&queue.active.size===1&&!jobs[0].cancelled);
    check('clear records leaves downloaded local files untouched',fs.readFileSync(path.join(moved,'fixture.png'),'utf8')==='local fixture media'&&fs.readFileSync(path.join(output,'kept.txt'),'utf8')==='do not delete');
    releaseOpen('late controlled failure');await wait(80);
    check('a late folder failure cannot resurrect a cleared task or leave an orphan error',queue.tasks.length===2&&await ui('!document.querySelector(".task-folder-error")'));
    check('clear records is disabled while only active and queued jobs remain',await ui('document.getElementById("clear-history").disabled'));
    jobs[0].resolve({});await until(()=>jobs.length===2,'queue continues after clearing history');
    check('completing a retained worker still advances the retained queue',queue.tasks.find(task=>task.id===waiting).status==='running');
    await until(()=>ui('!document.getElementById("clear-history").disabled'),'new completion can be cleared');
    await ui('document.getElementById("clear-history").click()');await until(()=>queue.tasks.length===1,'new completed record cleared');
    check('newly completed records can be cleared without interrupting their running neighbour',queue.tasks[0].id===waiting&&!jobs[1].cancelled);
    jobs[1].resolve({});await until(()=>queue.active.size===0&&ui('!document.getElementById("clear-history").disabled'),'last worker completed');
    await ui('document.getElementById("clear-history").click()');await until(()=>queue.tasks.length===0&&ui('!!document.querySelector("#tasks .empty")'),'empty history rendered');
    check('clearing the last record displays the empty state and a zero active count',await ui('document.getElementById("clear-history").disabled&&document.getElementById("task-count").textContent==="0"'));
    await require('node:assert/strict').rejects(()=>collector.invoke('open-folder',{id:'history-completed'}),/记录已清除/);
    check('removed records cannot silently open an unrelated default folder',true);
  } finally {
    releaseOpen?.('');queue.close();for(const job of jobs)job.resolve({});await wait(50);
    shell.openPath=opener;collector.queue=originalQueue;
    collector.settings={...settings};collector.state.login=login;collector.state.loginExpired=expired;
    collector.window.setSize(...size);if(!source.closed)collector.activateTab(source.id);
    collector.publish();collector.persist();if(!visible)collector.window.hide();
  }
}
module.exports={run};
