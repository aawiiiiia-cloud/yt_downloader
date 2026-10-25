'use strict';
const { navigationUrl, siteForUrl } = require('./model.cjs');

function isTaobaoLoginUrl(value) {
  try {
    const url = new URL(navigationUrl(value));
    return siteForUrl(url.href) === 'taobao' && (
      /^login\./i.test(url.hostname) || /\/(?:_____tmd_____|login_jump)(?:\/|$)/i.test(url.pathname) ||
      /\/login\.(?:jhtml|html?)(?:\/|$)/i.test(url.pathname));
  } catch { return false; }
}

// A blank child may be populated or navigated by its opener after window.open().
// Keep its native relationship, rather than issuing a second loadURL request.
function taobaoPopupKind(source, target) {
  if (siteForUrl(source) !== 'taobao') return null;
  if (target === 'about:blank') return 'login';
  try {
    const url = navigationUrl(target);
    if (siteForUrl(url) === 'taobao') return isTaobaoLoginUrl(url) ? 'login' : 'tab';
  } catch {}
  return null;
}

module.exports = { isTaobaoLoginUrl, taobaoPopupKind };
