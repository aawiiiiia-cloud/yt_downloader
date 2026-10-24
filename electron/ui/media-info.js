(function(root) {
  'use strict';
  function duration(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return '时长未知';
    seconds = Math.floor(seconds);
    const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds / 60) % 60, rest = seconds % 60;
    return (hours ? hours + ':' + String(minutes).padStart(2, '0') : String(minutes).padStart(2, '0')) + ':' + String(rest).padStart(2, '0');
  }
  function size(value, estimated = false) {
    if (!(value > 0)) return '大小未知';
    return (estimated ? '约 ' : '') + (value / 1000000).toFixed(2) + ' MB';
  }
  function videoSize(item, identifier = '', audioOnly = false) {
    if (audioOnly) return { size: item.duration > 0 ? item.duration * 192000 / 8 : null, estimated: true };
    const formats = [...(item.formats || [])].reverse();
    const audioOnlyStream = formats.find(row => row.audio && !row.video);
    const video = identifier ? formats.find(row => row.id === identifier) :
      (audioOnlyStream && formats.find(row => row.video && !row.audio)) || formats.find(row => row.video && row.audio) || formats.find(row => row.video);
    const audio = video && !video.audio ? formats.find(row => row.audio && !row.video) : null;
    const streams = [video, audio].filter(Boolean);
    if (!video) return { size: null, estimated: false };
    let total = 0, estimated = streams.length > 1;
    for (const stream of streams) {
      const bytes = stream.size || (stream.bitrate > 0 && item.duration > 0 ? stream.bitrate * 1000 * item.duration / 8 : null);
      if (!bytes) return { size: null, estimated: false };
      total += bytes; estimated ||= !stream.size || !!stream.sizeEstimated;
    }
    return { size: total, estimated };
  }
  const api = { duration, size, videoSize };
  if (typeof module !== 'undefined') module.exports = api;
  else root.mediaInfo = api;
})(globalThis);
