'use strict';
function workerProxy(route, configured = '') {
  if (configured) return configured;
  const first = String(route).split(';')[0].trim();
  if (first.toUpperCase() === 'DIRECT') return '';
  const match = first.match(/^(PROXY|HTTPS|SOCKS5?|SOCKS4)\s+(.+)$/i);
  if (!match) throw new Error('无法识别系统代理设置，请检查代理配置后重试');
  const kind = match[1].toUpperCase();
  const scheme = kind === 'SOCKS4' ? 'socks4a' : kind.startsWith('SOCKS') ? 'socks5h' : kind === 'HTTPS' ? 'https' : 'http';
  const endpoint = match[2].match(/^(\[[^\]]+\]|[A-Za-z0-9.-]+):([0-9]{1,5})$/);
  if (!endpoint || Number(endpoint[2]) < 1 || Number(endpoint[2]) > 65535) throw new Error('系统代理地址无效');
  const proxy = new URL(`${scheme}://${match[2]}`);
  if (!proxy.hostname || proxy.username || proxy.password || !['', '/'].includes(proxy.pathname) ||
      proxy.search || proxy.hash) throw new Error('系统代理地址无效');
  return `${scheme}://${proxy.hostname}:${Number(endpoint[2])}`;
}
module.exports = { workerProxy };
