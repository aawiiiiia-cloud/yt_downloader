'use strict';
const fs = require('node:fs'), path = require('node:path');
const { DownloadQueue } = require('./model.cjs');
const { failureError } = require('./task-failure.cjs');
const assert = require('node:assert/strict');
async function run(collector, check, until, wait, profile) {
  const original = collector.queue, runner = collector.runTask, tab = collector.tabs.activeId;
  const ui = code => collector.window.webContents.executeJavaScript(code);
  const cookie = value => collector.browserSession.cookies.set({ url: 'https://www.bilibili.com/', domain: '.bilibili.com',
    name: 'SESSDATA', value, path: '/', secure: true, httpOnly: true, expirationDate: Date.now() / 1000 + 3600 });
  let release, passed = false;
  const queue = new DownloadQueue((spec, emit) => collector.withCookies(spec, emit), () => collector.publish());
  collector.queue = queue;
  collector.runTask = (request, emit) => {
    let reject;
    const error = () => failureError(request.fixtureError, { operation: 'download' });
    if (request.fixtureHold) return { promise: new Promise((_, fail) => { reject = fail; release = () => fail(error()); }), cancel() { reject(error()); } };
    emit('log', 'ERROR: ' + request.fixtureError);
    return { promise: Promise.reject(error()), cancel() {} };
  };
  const add = (name, message, hold = false) => queue.add({ command: 'download', url: 'https://www.bilibili.com/video/BVfixture',
    settings: { ...collector.settings, proxy: 'http://127.0.0.1:9' }, fixtureError: message, fixtureHold: hold }, '受控失败：' + name);
  const finished = id => until(() => ['failed', 'cancelled'].includes(queue.tasks.find(row => row.id === id)?.status), 'failure fixture did not finish');
  try {
    await cookie('controlled-old-auth'); await collector.updateLogin();
    const cases = [
      ['网络超时', 'Read timed out', 'NETWORK_TIMEOUT'], ['网站限流', 'HTTP Error 429: Too Many Requests', 'RATE_LIMITED'],
      ['视频删除', 'This video has been deleted by the uploader', 'CONTENT_DELETED'],
      ['无访问权限', 'Private video. Sign in to watch', 'ACCESS_DENIED'],
      ['网站验证', 'Sign in to confirm you are not a bot', 'VERIFICATION_REQUIRED'],
      ['普通403', 'HTTP Error 403: Forbidden', 'ACCESS_DENIED'],
      ['未知错误', 'Unexpected response https://media.invalid/private?signature=fixture-private-url-token', 'UNKNOWN'],
    ];
    for (const [name, message, code] of cases) {
      const id = add(name, message); await finished(id);
      check('download failure retains its specific cause: ' + code, queue.tasks.find(row => row.id === id).failure.code === code);
    }
    check('network, rate limits, missing permissions and CAPTCHA cannot expire an otherwise detected login',
      collector.state.login.bilibili && !collector.publicState().loginExpired.bilibili);
    const old = add('旧凭证任务', 'Login required', true); await until(() => !!release, 'old snapshot did not start');
    await cookie('controlled-new-auth'); await collector.updateLogin(); release(); release = null; await finished(old);
    check('a failed old download cannot invalidate newly imported credentials', !collector.publicState().loginExpired.bilibili);
    const cancelled = add('手动取消', 'Cookies have expired', true); await until(() => !!release, 'cancellable task did not start');
    queue.cancel(cancelled); release = null; await finished(cancelled);
    check('manual cancellation remains separate from authentication failures', queue.tasks.find(row => row.id === cancelled).status === 'cancelled' && !collector.publicState().loginExpired.bilibili);
    const expired = add('凭证失效', 'Cookies have expired'); await finished(expired); await collector.updateLogin();
    check('explicit credential failure marks only the matching site and survives a cookie recheck',
      collector.publicState().loginExpired.bilibili && !collector.publicState().loginExpired.vimeo);
    collector.switchSite('bilibili');
    await until(() => ui("document.getElementById('login-state').textContent.includes('已检测到登录凭据') && document.getElementById('login-state').classList.contains('login-warning')"), 'rejected credentials header did not render');
    await ui("document.getElementById('tasks-tab').click()"); await wait(100);
    check('failed task cards show a Chinese reason and cancellation keeps its own message', await ui("(()=>{const cards=[...document.querySelectorAll('.task-card')];return cards.some(card=>card.textContent.includes('网络超时')&&card.querySelector('.task-error strong')?.textContent.includes('失败原因'))&&cards.some(card=>card.textContent.includes('手动取消')&&card.textContent.includes('操作已取消'))})()"));
    const bounds = await ui("(()=>{const r=document.querySelector('aside').getBoundingClientRect();return{x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)}})()");
    fs.writeFileSync(path.join(profile, '..', 'task-failure-panel.png'), (await collector.window.webContents.capturePage(bounds)).toPNG());
    check('failure records do not contain cookies or signed stream tokens', !/controlled-new-auth|fixture-private-url-token/.test(JSON.stringify(queue.list())));
    const verify = collector.verifyYouTube, update = collector.updateLogin;
    try {
      await collector.browserSession.cookies.set({ url: 'https://www.youtube.com/', domain: '.youtube.com', name: 'SID', value: 'controlled-before-verification', secure: true, expirationDate: Date.now() / 1000 + 3600 });
      await collector.updateLogin(); collector.verifyYouTube = async () => true;
      collector.updateLogin = async () => {
        await collector.browserSession.cookies.set({ url: 'https://www.youtube.com/', domain: '.youtube.com', name: 'SID', value: 'controlled-after-verification', secure: true, expirationDate: Date.now() / 1000 + 3600 });
        await update.call(collector);
      };
      await assert.rejects(() => collector.ensureYouTube(true), /状态已更新/);
      check('credentials changing during the final YouTube recheck cannot pass the download gate', collector.youtubeAuth.status !== 'verified');
    } finally {
      collector.verifyYouTube = verify; collector.updateLogin = update;
      await collector.browserSession.cookies.remove('https://www.youtube.com', 'SID'); await collector.updateLogin();
    }
    passed = true;
  } finally {
    collector.runTask = runner; collector.queue = original;
    if (passed) original.tasks.push(...queue.tasks);
    else queue.close();
    collector.activateTab(tab); collector.publish();
  }
}
module.exports = { run };
