'use strict';
(function(root) {
  const stages = { preparing: '准备下载', extracting: '获取下载地址', 'video-connecting': '连接视频',
    'image-connecting': '正在连接图片', 'image-receiving': '正在接收图片',
    'image-validating': '正在校验图片', 'image-converting': '正在转换图片',
    finished: '等待处理', processing: '正在合并或转换' };
  function formatBytes(value) {
    let bytes = Math.max(0, Number(value) || 0), unit = 0;
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    while (bytes >= 1024 && unit < units.length - 1) { bytes /= 1024; unit++; }
    return `${bytes.toFixed(unit ? 1 : 0)} ${units[unit]}`;
  }
  function taskProgress(task) {
    const indeterminate = task.percent == null;
    const percent = Math.max(0, Math.min(100, Number(task.percent) || 0));
    const bytes = Math.max(0, Number(task.downloadedBytes) || 0);
    return {
      stage: task.status === 'running' ? stages[task.detail] || '' : '',
      text: indeterminate ? (bytes ? `已接收 ${formatBytes(bytes)}` : ['running', 'cancelling'].includes(task.status) ? '等待数据' : '—')
        : `${Math.min(task.status === 'running' ? 99 : 100, Math.round(percent))}%`,
      attribute: indeterminate ? '' : ` value="${Math.min(task.status === 'running' ? 99 : 100, percent)}"`,
    };
  }
  function taskSpeed(task, now = Date.now()) {
    if (task.status === 'cancelling') return '正在取消';
    if (task.status !== 'running') return '';
    if (['image-validating', 'image-converting', 'processing', 'finished'].includes(task.detail))
      return stages[task.detail];
    if (['preparing', 'extracting', 'image-connecting', 'video-connecting'].includes(task.detail))
      return '等待连接';
    const speed = Number(task.speed), updated = Number(task.speedUpdatedAt);
    if (task.speed == null || !Number.isFinite(speed) || speed < 0 || !updated)
      return task.downloadedBytes > 0 ? '测速中' : '等待数据';
    if (now - updated >= 6000) return '等待数据';
    return `${formatBytes(speed)}/s`;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { taskProgress, taskSpeed, formatBytes };
  else { root.collectorTaskProgress = taskProgress; root.collectorTaskSpeed = taskSpeed; }
})(typeof window !== 'undefined' ? window : globalThis);
