'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { randomUUID } = require('node:crypto');
const { execFileSync } = require('node:child_process');
const ownedName = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.cookies\.txt$/i;

function plainDirectory(directory) {
  const info = fs.lstatSync(directory);
  if (!info.isDirectory() || info.isSymbolicLink() ||
      fs.realpathSync(directory).toLowerCase() !== path.resolve(directory).toLowerCase())
    throw Object.assign(new Error('Redirected credential directory'), { code: 'UNSAFE_PATH' });
}
const sidAliases = { SY: 'S-1-5-18', BA: 'S-1-5-32-544', BU: 'S-1-5-32-545', WD: 'S-1-1-0',
  AU: 'S-1-5-11', CO: 'S-1-3-0', CG: 'S-1-3-1', OW: 'S-1-3-4', AN: 'S-1-5-7' };
function nativeWindows(command, args) {
  return execFileSync(path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', command + '.exe'), args,
    { windowsHide: true, timeout: 15000, stdio: 'pipe' });
}
function windowsUserSid(run = nativeWindows) {
  const sid = run('whoami', ['/user', '/fo', 'csv', '/nh']).toString('utf8').trim()
    .match(/^"[^"]*","(S-\d-(?:\d+-)*\d+)"$/)?.[1];
  if (!sid) throw Object.assign(new Error('Cannot identify credential owner'), { code: 'ACL_IDENTITY_FAILED' });
  return sid;
}
function parseWindowsAcl(sddl) {
  const match = /^D:([A-Z]*)((?:\([^()]+\))*)$/.exec(sddl);
  if (!match || !match[2]) throw Object.assign(new Error('Cannot verify credential ACL'), { code: 'ACL_VERIFY_FAILED' });
  const rules = [...match[2].matchAll(/\(([^()]+)\)/g)].map(row => {
    const fields = row[1].split(';'), sid = sidAliases[fields[5]] || fields[5];
    if (fields.length !== 6 || !['A', 'D'].includes(fields[0]) || !/^S-\d-(?:\d+-)*\d+$/.test(sid))
      throw Object.assign(new Error('Unsupported credential ACL'), { code: 'ACL_VERIFY_FAILED' });
    return { allow: fields[0] === 'A', flags: fields[1], rights: fields[2], sid };
  });
  return { protected: match[1].includes('P'), rules };
}
function windowsAcl(file, run = nativeWindows) {
  const temporary = path.join(os.tmpdir(), `media-collector-${randomUUID()}.acl`);
  try {
    run('icacls', [file, '/save', temporary, '/q']);
    const bytes = fs.readFileSync(temporary);
    const data = bytes.toString(bytes[1] === 0 || (bytes[0] === 0xff && bytes[1] === 0xfe) ? 'utf16le' : 'utf8');
    const sddl = data.split(/\r?\n/).find(line => line.startsWith('D:'));
    return parseWindowsAcl(sddl || '');
  } finally { fs.rmSync(temporary, { force: true }); }
}
function protectDirectory(directory, run = nativeWindows) {
  if (process.platform !== 'win32') { fs.chmodSync(directory, 0o700); return; }
  const ids = [windowsUserSid(run), 'S-1-5-18', 'S-1-5-32-544'];
  // Native utilities also work under enterprise PowerShell language restrictions.
  run('icacls', [directory, '/inheritance:r', '/grant:r', ...ids.map(sid => `*${sid}:(OI)(CI)F`), '/q']);
  const unexpected = [...new Set(windowsAcl(directory, run).rules.filter(rule => rule.allow && !ids.includes(rule.sid)).map(rule => rule.sid))];
  if (unexpected.length) run('icacls', [directory, '/remove:g', ...unexpected.map(sid => '*' + sid), '/q']);
  const acl = windowsAcl(directory, run);
  if (!acl.protected || acl.rules.some(rule => rule.allow && !ids.includes(rule.sid)) ||
      ids.some(sid => !acl.rules.some(rule => rule.allow && rule.sid === sid &&
        rule.rights === 'FA' && rule.flags.includes('OI') && rule.flags.includes('CI'))))
    throw Object.assign(new Error('Unprotected credential ACL'), { code: 'ACL_VERIFY_FAILED' });
}
class TempCredentials {
  constructor(tasksDirectory, report, protect = protectDirectory) {
    this.root = tasksDirectory; this.directory = path.join(tasksDirectory, 'credentials');
    this.report = report; this.owned = new Set(); this.ready = false;
    try {
      fs.mkdirSync(this.root, { recursive: true }); plainDirectory(this.root);
      fs.mkdirSync(this.directory, { recursive: true, mode: 0o700 }); plainDirectory(this.directory);
      protect(this.directory);
      for (const directory of [this.root, this.directory]) {
        for (const name of fs.readdirSync(directory)) {
          if (ownedName.test(name)) fs.rmSync(path.join(directory, name), { force: true });
        }
      }
      this.ready = true; this.report('');
    } catch (error) { this.report(error.code || 'PROTECTION_FAILED'); }
  }
  reserve() {
    if (!this.ready) throw new Error('\u767b\u5f55\u51ed\u636e\u76ee\u5f55\u4fdd\u62a4\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u6743\u9650\u540e\u91cd\u542f');
    plainDirectory(this.directory);
    const file = path.join(this.directory, `${randomUUID()}.cookies.txt`);
    const fd = fs.openSync(file, 'wx', 0o600); fs.closeSync(fd);
    this.owned.add(file); return file;
  }
  write(text) {
    const file = this.reserve();
    try { fs.writeFileSync(file, text, { mode: 0o600 }); return file; }
    catch (error) { this.remove(file); throw error; }
  }
  remove(file) {
    if (!this.owned.has(file)) return;
    try { fs.rmSync(file, { force: true }); this.owned.delete(file); }
    catch (error) { this.report(error.code || 'CLEANUP_FAILED'); }
  }
  close() { for (const file of this.owned) this.remove(file); }
}
module.exports = { TempCredentials, protectDirectory, windowsAcl, windowsUserSid, parseWindowsAcl };
