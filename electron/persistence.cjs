'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

function writeJson(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.${process.pid}.${randomUUID()}.tmp`;
  let fd;
  try {
    fd = fs.openSync(temporary, 'wx', 0o600);
    fs.writeFileSync(fd, JSON.stringify(data, null, 2));
    fs.fsyncSync(fd);
    fs.closeSync(fd); fd = undefined;
    fs.renameSync(temporary, file);
  } finally {
    if (fd !== undefined) { try { fs.closeSync(fd); } catch {} }
    try { fs.rmSync(temporary, { force: true }); } catch {}
  }
}

class Persistence {
  constructor(directory, snapshot, report, options = {}) {
    this.directory = directory; this.snapshot = snapshot; this.report = report;
    this.write = options.write || writeJson;
    this.delay = options.delay ?? 400; this.retryDelay = options.retryDelay ?? 2000;
    this.saved = new Map(); this.closed = false;
  }
  schedule() {
    // Anchor to the first update, so continuous progress cannot postpone saving.
    if (this.closed || this.timer) return;
    this.timer = setTimeout(() => { this.timer = null; this.flush(); }, this.delay);
    this.timer.unref?.();
  }
  flush() {
    clearTimeout(this.timer); this.timer = null;
    const errors = [];
    let snapshot;
    try { snapshot = this.snapshot(); }
    catch (error) { errors.push(`snapshot (${error.code || 'ERROR'})`); }
    for (const [name, value] of Object.entries(snapshot || {})) {
      try {
        const serialized = JSON.stringify(value);
        if (serialized === this.saved.get(name)) continue;
        this.write(path.join(this.directory, name), value);
        this.saved.set(name, serialized);
      } catch (error) { errors.push(`${name} (${error.code || 'ERROR'})`); }
    }
    this.report(errors.length ? errors.join(', ') : '');
    if (errors.length && !this.closed) {
      this.timer = setTimeout(() => { this.timer = null; this.flush(); }, this.retryDelay);
      this.timer.unref?.();
    }
    return !errors.length;
  }
  close() { this.closed = true; return this.flush(); }
}
module.exports = { writeJson, Persistence };
