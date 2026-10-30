(function(root) {
  'use strict';
  function display(site, name, detected, expired) {
    const edgeLogin = site === 'youtube' || site === 'vimeo';
    if (!detected) return {
      text: `${name} · 【未检测到登录凭据】${edgeLogin ? '请使用下方Edge登录' : '建议登录'}`,
      title: edgeLogin
        ? '未检测到本站登录凭据，请使用下方 Edge 登录。'
        : '未检测到本站登录凭据，建议登录；网站当前状态请以左侧网页为准。',
      warning: true, success: false,
    };
    return {
      text: `${name} · 【已检测到登录凭据】如左侧网页显示未登录，请${edgeLogin ? '重新使用下方Edge登录' : '重新登录'}`,
      title: edgeLogin
        ? '右侧仅表示已检测到本地登录凭据；如左侧网页显示未登录，请重新使用下方 Edge 登录。'
        : '右侧仅表示已检测到本地登录凭据；如左侧网页显示未登录，请重新登录。',
      warning: !!expired, success: false,
    };
  }
  const api = { display };
  if (typeof module !== 'undefined') module.exports = api;
  else root.loginStatus = api;
})(globalThis);
