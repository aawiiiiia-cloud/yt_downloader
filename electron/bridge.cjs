'use strict';
const { spawn, execFile } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');

function pythonExecutable() {
  const candidates = [process.env.YT_DOWNLOADER_PYTHON, 'D:\\python-envs\\yt-downloader\\Scripts\\python.exe'];
  for (const candidate of candidates) if (candidate && fs.existsSync(candidate)) return candidate;
  throw new Error('未找到项目 Python 环境。请设置 YT_DOWNLOADER_PYTHON 后重新启动。');
}
function pythonTask(request, onEvent = () => {}, options = {}) {
  const root = path.resolve(__dirname, '..');
  const executable = options.python || pythonExecutable();
  const child = spawn(executable, ['-B', '-u', '-X', 'utf8', path.join(root, 'collector_worker.py')], {
    cwd: root, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
  });
  let timer, terminal, stderr = '', cancelled = false, failure;
  const promise = new Promise((resolve, reject) => {
    const lines = readline.createInterface({ input: child.stdout });
    lines.on('line', line => {
      try {
        const event = JSON.parse(line);
        if (event.type === 'result') terminal = event.data;
        else if (event.type === 'error' || event.type === 'cancelled') {
          failure = new Error(String(event.data)); failure.cancelled = event.type === 'cancelled';
        } else onEvent(event.type, event.data);
      } catch { /* Ignore unstructured third-party diagnostics; never surface secrets. */ }
    });
    child.stderr.on('data', data => { stderr = (stderr + data).slice(-4000); });
    child.on('error', reject);
    child.stdin.on('error', () => {});
    child.on('close', code => {
      clearTimeout(timer);
      if (cancelled && !failure) { failure = new Error('操作已取消'); failure.cancelled = true; }
      if (failure) reject(failure);
      else if (code !== 0 || terminal === undefined) reject(new Error('解析进程未能完成，请检查 Python 依赖或稍后重试'));
      else resolve(terminal);
    });
    child.stdin.write(JSON.stringify(request) + '\n');
  });
  return { promise, pid: child.pid, cancel() {
    if (cancelled || child.exitCode !== null) return;
    cancelled = true; child.stdin.write('{"command":"cancel"}\n');
    timer = setTimeout(() => {
      if (process.platform === 'win32' && child.pid) execFile('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true }, () => {});
      else child.kill('SIGKILL');
    }, 40000); timer.unref();
  } };
}
module.exports = { pythonExecutable, pythonTask };
