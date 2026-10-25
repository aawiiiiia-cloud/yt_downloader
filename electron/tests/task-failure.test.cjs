'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const { classifyFailure, failureError, safeDetail } = require('../task-failure.cjs');
const { DownloadQueue } = require('../model.cjs');
const { pythonTask } = require('../bridge.cjs');
const tick = () => new Promise(resolve => setImmediate(resolve));
test('download failures expose actionable categories without guessing why an HTTP URL failed', () => {
  const cases = [
    ['The provided YouTube account cookies are no longer valid', 'AUTH_EXPIRED'],
    ['Your login session has expired', 'AUTH_EXPIRED'],
    ['登录凭证已失效/过期，请重新登录', 'AUTH_EXPIRED'],
    ['HTTP Error 429: Too Many Requests', 'RATE_LIMITED'],
    ['Read timed out', 'NETWORK_TIMEOUT'],
    ['Connection reset by peer', 'NETWORK_ERROR'],
    ['HTTP Error 503: Service Unavailable', 'SERVER_ERROR'],
    ['This video has been deleted by the uploader', 'CONTENT_DELETED'],
    ['视频已下架', 'CONTENT_DELETED'],
    ['HTTP Error 404: Not Found', 'CONTENT_UNAVAILABLE'],
    ['Video unavailable', 'CONTENT_UNAVAILABLE'],
    ['Private video. Sign in to watch this video', 'ACCESS_DENIED'],
    ['This video is only available to members of this channel', 'ACCESS_DENIED'],
    ['HTTP Error 403: Forbidden', 'ACCESS_DENIED'],
    ['Sign in to confirm you’re not a bot', 'VERIFICATION_REQUIRED'],
    ['Fresh cookies (not necessarily logged in) are needed', 'VERIFICATION_REQUIRED'],
    ['Signature has expired', 'LINK_EXPIRED'],
    ['Login required', 'AUTH_REQUIRED'],
    ['Requested format is not available', 'FORMAT_UNAVAILABLE'],
    ['ffmpeg not found', 'TOOL_MISSING'],
    ['No space left on device', 'SAVE_FAILED'],
    ['An undocumented service response', 'UNKNOWN'],
  ];
  for (const [message, expected] of cases) assert.equal(classifyFailure({ message }).code, expected, message);
});
test('credential rejection needs explicit authentication evidence, not a timeout, CAPTCHA, permissions or expired media URL', () => {
  for (const message of ['HTTP Error 403: Forbidden', 'HTTP Error 404: Not Found', 'Read timed out', 'CAPTCHA login challenge', 'Signature has expired'])
    assert.notEqual(classifyFailure({ message }, [], { hadCredentials: true }).code, 'AUTH_EXPIRED', message);
  assert.equal(classifyFailure({ message: 'Login required' }, [], { hadCredentials: true }).code, 'AUTH_EXPIRED');
  assert.equal(classifyFailure({ message: 'HTTP Error 429: retry after login' }).code, 'RATE_LIMITED');
});
test('structured causes survive worker messages and sensitive URLs and headers are redacted', () => {
  const error = failureError({ message: 'wrapper', kind: 'timeout' }, { operation: 'download' });
  assert.equal(error.code, 'NETWORK_TIMEOUT'); assert.match(error.message, /网络连接超时/);
  assert.equal(failureError({ message: 'request failed', httpStatus: 429 }).code, 'RATE_LIMITED');
  assert.equal(failureError({ message: 'permission denied', kind: 'filesystem' }).code, 'SAVE_FAILED');
  assert.equal(failureError({ message: 'Read timed out; earlier warning: cookies have expired', kind: 'timeout' }).code, 'NETWORK_TIMEOUT');
  assert.equal(failureError({ message: 'HTTP Error 403: Forbidden', httpStatus: 403 }).code, 'ACCESS_DENIED');
  const text = safeDetail('ERROR: https://user:password@cdn.invalid/secret-path?signature=private\nAuthorization: Bearer secret\nCookie: SESSDATA=secret');
  assert.doesNotMatch(text, /password|secret-path|private|Bearer secret|SESSDATA=secret/);
});
test('only final error evidence can refine a generic failure; an earlier warning cannot expire credentials', () => {
  const logs = ['WARNING: account cookies have expired', '[download] retrying', 'ERROR: HTTP Error 429: Too Many Requests'];
  assert.equal(classifyFailure(new Error('下载未完成，请查看任务日志后重试'), logs).code, 'RATE_LIMITED');
  assert.equal(classifyFailure(new Error('unknown'), logs.slice(0, 1)).code, 'UNKNOWN');
});
test('task cards retain classified reasons and raw redacted details; retries use a fresh task', async () => {
  let attempt = 0;
  const queue = new DownloadQueue(() => ({ promise: ++attempt === 1 ? Promise.reject(failureError({ message: 'HTTP Error 429: Too Many Requests' }, { operation: 'download' })) : Promise.resolve({}), cancel() {} }), () => {});
  const id = queue.add({ url: 'fixture' }, '限流任务'); await tick();
  const failed = queue.tasks[0]; assert.equal(failed.failure.code, 'RATE_LIMITED'); assert.match(failed.error, /限流/);
  assert.ok(failed.logs.some(line => line.includes('429')));
  queue.retry(id); await tick(); assert.equal(queue.tasks[0].status, 'completed'); assert.equal(queue.tasks[0].error, undefined);
  assert.equal(JSON.parse(JSON.stringify(queue.list()))[1].failure.code, 'RATE_LIMITED');
});
test('manual cancellation remains cancellation even when the network fails at the same time', async () => {
  let reject;
  const queue = new DownloadQueue(() => ({ promise: new Promise((_, fail) => { reject = fail; }), cancel() {} }), () => {});
  const id = queue.add({}, '取消任务'), waiting = queue.add({}, '排队取消任务');
  queue.cancel(waiting); assert.match(queue.tasks[0].error, /操作已取消/);
  queue.cancel(id); reject(new Error('HTTP Error 403: Forbidden')); await tick();
  assert.equal(queue.tasks[1].status, 'cancelled'); assert.equal(queue.tasks[1].failure.code, 'CANCELLED');
});
test('a real Python worker error crosses the bridge as a classified failure without starting a download', async () => {
  const task = pythonTask({ command: 'download', url: 'https://www.youtube.com/watch?v=fixture', settings: {} });
  await assert.rejects(task.promise, error => {
    assert.equal(error.failure.code, 'AUTH_REQUIRED'); assert.match(error.message, /登录/); return true;
  });
});
