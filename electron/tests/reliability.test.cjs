'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os'), vm = require('node:vm');
const { createRequire } = require('node:module');
const { Persistence, writeJson } = require('../persistence.cjs');
const { TempCredentials, protectDirectory, windowsAcl, windowsUserSid, parseWindowsAcl } = require('../temp-credentials.cjs');
const { acquireInstance } = require('../single-instance.cjs');
const { makeRecovery, validateRecovery, recoveredSelection, sourceUrl } = require('../task-recovery.cjs');
const { DownloadQueue } = require('../model.cjs');
const tick = () => new Promise(resolve => setImmediate(resolve));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function temporary(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'collector-reliability-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}
test('atomic JSON replacement leaves a complete value and no temporary files', t => {
  const root = temporary(t), file = path.join(root, 'settings.json');
  writeJson(file, { value: 1 }); writeJson(file, { value: 2 });
  assert.deepEqual(JSON.parse(fs.readFileSync(file, 'utf8')), { value: 2 });
  assert.deepEqual(fs.readdirSync(root), ['settings.json']);
  assert.throws(() => writeJson(path.join(root, 'absent'), { big: 1n }));
  assert.deepEqual(fs.readdirSync(root), ['settings.json']);
});
test('continuous progress does not postpone the first save', async t => {
  let count = 0, writes = 0;
  const storage = new Persistence(temporary(t), () => ({ 'history.json': { count } }), () => {},
    { delay: 25, write: () => writes++ });
  t.after(() => storage.close());
  const first = storage.timer;
  storage.schedule(); const timer = storage.timer;
  assert.notEqual(timer, first);
  for (let i = 0; i < 5; i++) { count++; storage.schedule(); assert.equal(storage.timer, timer); }
  await delay(60); assert.equal(writes, 1);
  storage.flush(); assert.equal(writes, 1);
});
test('a failed file does not block other files and retries clear the warning', async t => {
  let blocked = true; const reports = [], writes = [];
  const storage = new Persistence(temporary(t), () => ({ 'settings.json': {}, 'history.json': [] }),
    error => reports.push(error), { retryDelay: 20, write: file => {
      if (path.basename(file) === 'settings.json' && blocked)
        throw Object.assign(new Error('must not expose credentials in an error message'), { code: 'ENOSPC' });
      writes.push(path.basename(file));
    } });
  t.after(() => storage.close());
  assert.equal(storage.flush(), false);
  assert.deepEqual(writes, ['history.json']); assert.equal(reports[0], 'settings.json (ENOSPC)');
  blocked = false;
  await delay(70);
  assert.deepEqual(writes, ['history.json', 'settings.json']); assert.equal(reports.at(-1), '');
});
test('snapshot failure and shutdown disk failure are caught without a retry timer', t => {
  const storage = new Persistence(temporary(t), () => { throw Object.assign(new Error('private'), { code: 'EACCES' }); }, () => {});
  assert.equal(storage.close(), false); assert.equal(storage.timer, null);
  storage.schedule(); assert.equal(storage.timer, null);
});
test('native ACL parsing accepts protected SID grants and rejects unverifiable rules', () => {
  const acl = parseWindowsAcl('D:PAI(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)(A;OICI;FA;;;S-1-5-21-1-2-3-1001)');
  assert.equal(acl.protected, true); assert.equal(acl.rules[0].sid, 'S-1-5-32-544');
  assert.equal(acl.rules[1].sid, 'S-1-5-18');
  assert.equal(parseWindowsAcl('D:AI(A;OICI;FR;;;WD)').protected, false);
  for (const value of ['', 'D:P', 'D:P(XA;OICI;FA;;;WD)', 'D:P(A;OICI;FA;;;unrecognized)'])
    assert.throws(() => parseWindowsAcl(value), { code: 'ACL_VERIFY_FAILED' });
});
test('native owner lookup validates the machine-readable identity without account-name assumptions', () => {
  assert.equal(windowsUserSid(() => Buffer.from('"DOMAIN,user","S-1-5-21-1-2-3-1001"\r\n')), 'S-1-5-21-1-2-3-1001');
  assert.throws(() => windowsUserSid(() => Buffer.from('"user","not-a-sid"')), { code: 'ACL_IDENTITY_FAILED' });
});
test('icacls metadata reads support UTF-16 with and without a BOM and clean temporary files', t => {
  const file = path.join(temporary(t), 'credentials');
  for (const prefix of ['', '\ufeff']) {
    let temporaryFile;
    const acl = windowsAcl(file, (command, args) => {
      assert.equal(command, 'icacls'); assert.equal(args[1], '/save');
      temporaryFile = args[2];
      fs.writeFileSync(temporaryFile, prefix + 'credentials\r\nD:PAI(A;OICI;FA;;;SY)\r\n', 'utf16le');
      return Buffer.alloc(0);
    });
    assert.equal(acl.protected, true); assert.equal(fs.existsSync(temporaryFile), false);
  }
});
test('native credential protection removes unexpected explicit grants and verifies inherited file rights',
  { skip: process.platform !== 'win32' }, t => {
    const directory = temporary(t), sid = 'S-1-5-21-1-2-3-1001';
    let unexpected = true, grants = false, removed = false;
    protectDirectory(directory, (command, args) => {
      if (command === 'whoami') return Buffer.from(`"user","${sid}"`);
      assert.equal(command, 'icacls');
      if (args.includes('/grant:r')) {
        assert.ok(args.includes('/inheritance:r')); assert.ok(args.includes(`*${sid}:(OI)(CI)F`)); grants = true;
      } else if (args.includes('/remove:g')) {
        assert.ok(args.includes('*S-1-1-0')); unexpected = false; removed = true;
      } else if (args.includes('/save')) {
        fs.writeFileSync(args[2], `credentials\r\nD:PAI(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)(A;OICI;FA;;;${sid})${unexpected ? '(A;;FR;;;WD)' : ''}\r\n`, 'utf16le');
      }
      return Buffer.alloc(0);
    });
    assert.equal(grants, true); assert.equal(removed, true);
  });
test('startup removes only application-owned stale cookie files from both directories', t => {
  const root = temporary(t), child = path.join(root, 'credentials');
  fs.mkdirSync(child);
  const own = '11111111-1111-4111-8111-111111111111.cookies.txt';
  for (const dir of [root, child]) {
    fs.writeFileSync(path.join(dir, own), 'controlled fake cookie');
    fs.writeFileSync(path.join(dir, 'user-cookies.txt'), 'keep');
  }
  const reports = [], store = new TempCredentials(root, error => reports.push(error), () => {});
  assert.equal(store.ready, true); assert.deepEqual(reports, ['']);
  for (const dir of [root, child]) {
    assert.equal(fs.existsSync(path.join(dir, own)), false);
    assert.equal(fs.existsSync(path.join(dir, 'user-cookies.txt')), true);
  }
  const a = store.write('fake-a'), b = store.reserve();
  assert.notEqual(a, b); assert.equal(path.dirname(a), child);
  const external = path.join(root, 'user-cookies.txt'); store.remove(external);
  assert.equal(fs.existsSync(external), true);
  store.remove(a); assert.equal(fs.existsSync(a), false);
  store.close(); assert.equal(fs.existsSync(b), false);
});
test('directory protection or stale cleanup failure disables credential export', t => {
  const root = temporary(t), reports = [];
  const denied = new TempCredentials(root, code => reports.push(code), () => {
    throw Object.assign(new Error('do not disclose output'), { code: 'EACCES' });
  });
  assert.equal(denied.ready, false); assert.throws(() => denied.write('fake'));
  assert.deepEqual(reports, ['EACCES']);
  const other = path.join(root, 'other'); fs.mkdirSync(other);
  fs.mkdirSync(path.join(other, '11111111-1111-4111-8111-111111111111.cookies.txt'));
  const incomplete = new TempCredentials(other, () => {}, () => {});
  assert.equal(incomplete.ready, false); assert.throws(() => incomplete.reserve());
});
test('redirected credential directory is refused before protection or cleanup', t => {
  const root = temporary(t), actual = path.join(root, 'actual'), tasks = path.join(root, 'tasks');
  fs.mkdirSync(actual); fs.mkdirSync(tasks);
  fs.symlinkSync(actual, path.join(tasks, 'credentials'), process.platform === 'win32' ? 'junction' : 'dir');
  let protectedDirectory = false;
  const store = new TempCredentials(tasks, () => {}, () => { protectedDirectory = true; });
  assert.equal(store.ready, false); assert.equal(protectedDirectory, false);
  assert.throws(() => store.reserve());
});
test('credential cleanup failures are reported, retained for retry and do not throw', t => {
  const store = new TempCredentials(temporary(t), () => {}, () => {});
  const file = store.reserve();
  fs.unlinkSync(file); fs.mkdirSync(file); fs.writeFileSync(path.join(file, 'block'), 'controlled');
  assert.doesNotThrow(() => store.remove(file)); assert.equal(store.owned.has(file), true);
  fs.rmSync(file, { recursive: true }); store.close();
  assert.equal(store.owned.size, 0);
});
test('secondary instance quits before initialization; primary restores its existing window', () => {
  let quit = false, handler, focused = 0, shown = 0, restored = 0;
  assert.equal(acquireInstance({ requestSingleInstanceLock: () => false, quit: () => { quit = true; } }, () => {
    throw new Error('must not initialize');
  }), false);
  assert.equal(quit, true);
  let window = { isDestroyed: () => false, isMinimized: () => true,
    restore: () => restored++, show: () => shown++, focus: () => focused++ };
  assert.equal(acquireInstance({ requestSingleInstanceLock: () => true, on: (name, fn) => {
    assert.equal(name, 'second-instance'); handler = fn;
  } }, () => window), true);
  handler(); assert.deepEqual([restored, shown, focused], [1, 1, 1]);
  window = null; assert.doesNotThrow(handler);
});
const url = 'https://www.xiaohongshu.com/explore/6a8fbc2400000000070063fc';
function imageItem() {
  return { id: 'note', url, kind: 'images', title: 'fixture',
    images: [1, 2].map(index => ({ index, url: `https://cdn.example/image-${index}?signature=old` })) };
}
const settings = { saveDir: path.resolve(os.tmpdir()), imageFormat: 'original', imageQuality: 90,
  proxy: 'http://proxy.invalid', password: 'must not persist' };
function descriptor(item = imageItem(), data = { indices: [2] }) {
  return makeRecovery({ url: url + '?xsec_token=secret', context: { private: 'secret' } }, item, data, settings);
}
test('recovery metadata contains choices but no cookies, signed links, proxy or page context', () => {
  const recovery = descriptor();
  assert.equal(recovery.url, url); assert.deepEqual(recovery.images.map(row => row.index), [2]);
  const json = JSON.stringify(recovery);
  for (const privateValue of ['secret', 'signature', 'cdn.example', 'proxy', 'password', 'context', 'cookieFile'])
    assert.equal(json.includes(privateValue), false);
  const validated = validateRecovery({ ...recovery, cookieFile: 'secret', context: 'secret', spec: 'secret' });
  assert.deepEqual(validated, recovery);
});
test('source URLs retain Tmall routing and strip access hashes and tracking', () => {
  assert.equal(sourceUrl('https://detail.tmall.com/item.htm?id=123&token=secret'), 'https://detail.tmall.com/item.htm?id=123');
  assert.equal(sourceUrl('https://chaoshi.detail.tmall.com/item.htm?id=123'), 'https://chaoshi.detail.tmall.com/item.htm?id=123');
  assert.equal(sourceUrl('https://vimeo.com/123/abc123'), 'https://vimeo.com/123');
  assert.equal(sourceUrl('https://www.instagram.com/reels/DfLfK7uIpU8/?img_index=4'), 'https://www.instagram.com/p/DfLfK7uIpU8/');
  assert.equal(sourceUrl('https://www.instagram.com/search/'), null);
  assert.equal(sourceUrl('https://www.instagram.com.evil.test/p/DfLfK7uIpU8/'), null);
});
test('fresh signed image URLs are accepted but changed images, IDs and order are not', () => {
  const recovery = descriptor(), item = imageItem();
  item.images[1].url = 'https://different-cdn.example/image-2?signature=fresh';
  assert.deepEqual(recoveredSelection(recovery, { items: [item] }).indices, [2]);
  item.images.reverse(); item.images.forEach((row, i) => { row.index = i + 1; });
  assert.throws(() => recoveredSelection(recovery, { items: [item] }));
  assert.throws(() => recoveredSelection(recovery, { items: [{ ...imageItem(), id: 'other' }] }));
  assert.throws(() => recoveredSelection(recovery, { items: [] }));
});
test('corrupt recovery descriptors are rejected without throwing', () => {
  const valid = descriptor();
  for (const value of [null, {}, { ...valid, images: [null] }, { ...valid, images: [...valid.images, ...valid.images] },
    { ...valid, images: [{ index: 1, keys: ['secret'] }] }, { ...valid, settings: { ...settings, saveDir: 'relative' } },
    { ...valid, settings: null }, { ...valid, settings: { ...settings, saveDir: 123 } },
    { ...valid, settings: { ...settings, saveDir: {} } }, { ...valid, settings: { ...settings, saveDir: [] } },
    { ...valid, quality: { width: NaN } }]) assert.equal(validateRecovery(value), null);
});
test('video recovery reselects identical quality with fresh format IDs', () => {
  const item = { id: 'video', url, kind: 'video', formats: [{ id: 'old', height: 1080, width: 1920, fps: 30, ext: 'mp4', audio: true, video: true }] };
  const recovery = descriptor(item, { formatId: 'old', audioOnly: true, subtitles: true });
  const fresh = { ...item, formats: [{ ...item.formats[0], id: 'new' }] };
  const selection = recoveredSelection(recovery, { items: [fresh] });
  assert.equal(selection.formatId, 'new'); assert.equal(selection.audioOnly, true); assert.equal(selection.subtitles, true);
  assert.throws(() => recoveredSelection(recovery, { items: [{ ...fresh, formats: [] }] }));
});
const mainPath = path.resolve(__dirname, '../main.cjs'), localRequire = createRequire(mainPath), exported = { exports: {} };
vm.runInNewContext(fs.readFileSync(mainPath, 'utf8'), {
  require: name => name === 'electron' ? { app: { setName() {}, setPath() {}, on() {}, quit() {},
    requestSingleInstanceLock: () => true, whenReady: () => new Promise(() => {}) } } : localRequire(name),
  module: exported, __dirname: path.dirname(mainPath), process, console, URL, Buffer,
  AbortController, setTimeout, clearTimeout, setInterval, clearInterval,
}, { filename: mainPath });
const { CollectorApp } = exported.exports;
test('task context tolerates legacy specs without output settings', () => {
  const queue = new DownloadQueue(() => {}, () => {});
  queue.tasks = [
    { id: 'legacy-spec', status: 'failed', spec: { url } },
    { id: 'legacy-history', status: 'failed' },
  ];
  const app = Object.assign(Object.create(CollectorApp.prototype), {
    tabs: { active: { state: { url, loginExpired: {} } }, rows: [], activeId: 'tab' },
    queue, settings: {}, cache: new Map(), youtubeAuth: { status: 'unknown' },
  });
  const tasks = app.publicState().tasks;
  assert.equal(tasks[0].source, url);
  assert.equal(tasks[0].targetDir, '');
  assert.equal(tasks[0].canRetry, true);
  assert.equal(tasks[1].targetDir, '');
  assert.equal(tasks[1].canRetry, false);
});
function retryFixture(t, options = {}) {
  const item = imageItem(), recovery = descriptor();
  recovery.settings.saveDir = temporary(t);
  const task = { id: 'old-task', status: 'failed', recovery };
  const tab = { id: 'tab', state: { url, loading: false, parsing: false }, closed: false,
    view: { webContents: { getURL: () => url } } };
  const app = Object.assign(Object.create(CollectorApp.prototype), {
    tabs: { rows: [tab], active: tab }, handles: new Set(), closing: false, queue: { tasks: [task] },
    publish() {}, invalidate() {}, activateTab: () => tab,
    openTab() { throw new Error('should reuse tab'); }, readPage: async () => true,
    inspect: async () => { tab.state.result = { items: [item] }; tab.state.inspectionId = 'fresh-receipt'; },
    resultMatchesPage: () => true, download: async (selection, recovered) => {
      assert.equal(selection.inspectionId, 'fresh-receipt'); assert.deepEqual(Array.from(selection.indices), [2]);
      assert.equal(recovered.saveDir, recovery.settings.saveDir); return 'new-task';
    }, ...options,
  });
  return { app, tab, task, item };
}
test('restart retry without an in-memory spec parses a fresh receipt and preserves choices', async t => {
  const f = retryFixture(t);
  assert.equal(await f.app.retryTask('old-task'), 'new-task');
  assert.equal(f.task.recovering, false); await tick(); assert.equal(f.app.handles.size, 0);
});
test('retry waits for a new document but not unrelated loading resources', async t => {
  const f = retryFixture(t); f.tab.state.loading = true;
  assert.equal(await f.app.retryTask('old-task'), 'new-task');
  assert.equal(f.task.recovering, false);
});
test('duplicate retry and user navigation during parsing cannot enqueue stale media', async t => {
  const f = retryFixture(t), pending = Promise.withResolvers();
  f.app.inspect = () => pending.promise;
  const retry = f.app.retryTask('old-task'); await tick();
  assert.throws(() => f.app.retryTask('old-task'));
  f.tab.state.url = url.replace('6a8fbc24', '6b8fbc24'); pending.resolve();
  await assert.rejects(retry); assert.equal(f.task.recovering, false);
});
test('cancel recovery settles before download and a cleared history cannot remove it midway', async t => {
  const f = retryFixture(t), pending = Promise.withResolvers();
  f.app.inspect = () => pending.promise;
  const retry = f.app.retryTask('old-task'); await tick();
  const queue = new DownloadQueue(() => {}, () => {}); queue.tasks = [f.task];
  assert.equal(queue.clearFinished(), 0);
  [...f.app.handles][0].cancel(); pending.resolve();
  await assert.rejects(retry, error => error.cancelled === true);
  assert.equal(f.task.recovering, false); assert.equal(queue.clearFinished(), 1);
});
test('missing original output directory fails recovery without opening pages', async t => {
  const f = retryFixture(t); f.task.recovery.settings.saveDir = path.join(f.task.recovery.settings.saveDir, 'missing');
  await assert.rejects(f.app.retryTask('old-task')); assert.equal(f.task.recovering, false);
});
