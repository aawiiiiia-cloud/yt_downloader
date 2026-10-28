'use strict';
const fs = require('node:fs');

async function openFolder(folder, openPath, task = false) {
  const missing = task ? '下载目录不存在，可能已被删除或移动。' : '保存目录尚不存在，请先下载资源或重新选择保存位置。';
  if (typeof folder !== 'string' || !folder) throw new Error(missing);
  try {
    if (!fs.statSync(folder).isDirectory()) throw Object.assign(new Error(), { code: 'ENOTDIR' });
  } catch (error) {
    if (['ENOENT', 'ENOTDIR'].includes(error.code)) throw new Error(missing);
    throw new Error('无法访问目录，请检查磁盘连接或访问权限。');
  }
  try {
    // Electron resolves with an error string when the OS cannot open a path.
    const error = await openPath(folder);
    if (error) throw new Error();
  } catch {
    throw new Error('无法打开目录，目录可能已被移动或没有访问权限。');
  }
}
module.exports = { openFolder };
