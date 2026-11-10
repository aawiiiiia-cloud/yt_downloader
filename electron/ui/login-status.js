(function(root) {
  'use strict';
  function display(site, name, detected, expired) {
    const edgeLogin = site === 'youtube' || site === 'vimeo';
    if (expired) return {
      text: `${name} \u00b7 \u51ed\u636e\u5df2\u5931\u6548\uff0c\u8bf7\u91cd\u65b0\u767b\u5f55`,
      title: '\u672c\u6b21\u8bf7\u6c42\u5df2\u786e\u8ba4\u767b\u5f55\u51ed\u636e\u5931\u6548\uff0c\u8bf7\u91cd\u65b0\u767b\u5f55\u540e\u89e3\u6790',
      warning: true, success: false,
    };
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
