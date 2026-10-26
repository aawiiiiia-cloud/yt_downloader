'use strict';
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const executable = require('electron');
const root = path.resolve(__dirname, '..');
const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE;
let result = spawnSync(executable, ['.', '--collector-smoke'], { cwd: root, env, stdio: 'inherit', timeout: 120000, windowsHide: true });
if (result.status !== 0) process.exit(result.status || 1);
const report = JSON.parse(fs.readFileSync(path.join(root, 'electron-artifacts', 'smoke-result.json'), 'utf8'));
result = spawnSync(executable, ['.', '--collector-persistence-check'], {
  cwd: root, env: { ...env, YT_COLLECTOR_HOME: report.profile }, stdio: 'inherit', timeout: 30000, windowsHide: true,
});
process.exit(result.status || (result.error ? 1 : 0));
