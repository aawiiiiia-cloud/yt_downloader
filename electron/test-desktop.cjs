'use strict';
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const executable = require('electron');
const root = path.resolve(__dirname, '..');
const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE;
// Tests must paint their isolated views even while another desktop window
// overlaps them. Keep native hit testing/capture active; production startup
// retains Chromium's default background and occlusion behavior.
const testFlags = ['--disable-backgrounding-occluded-windows', '--disable-renderer-backgrounding',
  '--disable-features=CalculateNativeWinOcclusion'];
const reliabilityOnly = process.argv.includes('--reliability-only');
let result = spawnSync(executable, [...testFlags, '.', '--collector-smoke', ...(reliabilityOnly ? ['--collector-reliability-only'] : [])],
  { cwd: root, env, stdio: 'inherit', timeout: 600000, windowsHide: true });
if (result.status !== 0) {
  if (result.error) console.error(result.error.message);
  process.exit(result.status || 1);
}
if (reliabilityOnly) process.exit(0);
const report = JSON.parse(fs.readFileSync(path.join(root, 'electron-artifacts', 'smoke-result.json'), 'utf8'));
result = spawnSync(executable, [...testFlags, '.', '--collector-persistence-check'], {
  cwd: root, env: { ...env, YT_COLLECTOR_HOME: report.profile }, stdio: 'inherit', timeout: 30000, windowsHide: true,
});
process.exit(result.status || (result.error ? 1 : 0));
