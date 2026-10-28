'use strict';

// These are first-party login entry points, not authentication requests. Account
// fields, CAPTCHA, consent and submission remain entirely user controlled.
const WEB_LOGIN = {
  bilibili: { url: 'https://passport.bilibili.com/login', direct: true },
  xiaohongshu: { url: 'https://www.xiaohongshu.com/explore' },
  douyin: { url: 'https://www.douyin.com/' },
  behance: { url: 'https://www.behance.net/login' },
  taobao: { url: 'https://login.taobao.com/member/login.jhtml', direct: true },
  zcool: { url: 'https://passport.zcool.com.cn/loginApp.do?appId=1006&cback=https%3A%2F%2Fwww.zcool.com.cn%2F', direct: true },
};

function loginPlan(site) {
  if (!Object.hasOwn(WEB_LOGIN, site)) throw new Error('本站请使用 Edge 登录或导入 Cookies');
  return WEB_LOGIN[site];
}

function loginHost(site, value) {
  let url; try { url = new URL(value); } catch { return false; }
  if (url.protocol !== 'https:') return false;
  const host = url.hostname;
  if (site === 'bilibili') return host === 'passport.bilibili.com';
  if (site === 'zcool') return host === 'passport.zcool.com.cn';
  if (site === 'taobao') return host === 'login.taobao.com' || host === 'login.tmall.com';
  if (site === 'behance') return ['auth.services.adobe.com', 'auth-light.identity.adobe.com', 'adobeid-na1.services.adobe.com', 'ims-na1.adobelogin.com'].includes(host);
  return false;
}

// Serialized into the sandboxed webpage. Only known navigation controls may be
// clicked; a form's "登录" / "继续" / "获取验证码" button is never a trigger.
function loginSurface(site, trigger, isAuthHost) {
  const roots = [document];
  for (let index = 0; index < roots.length && index < 100; index++) {
    for (const node of [...roots[index].querySelectorAll('*')].slice(0, 8000)) if (node.shadowRoot) roots.push(node.shadowRoot);
  }
  const query = selector => roots.slice(0, 100).flatMap(root => [...root.querySelectorAll(selector)]);
  const visible = node => !!node && node.getBoundingClientRect().width > 0 && node.getBoundingClientRect().height > 0 &&
    getComputedStyle(node).visibility !== 'hidden' && getComputedStyle(node).display !== 'none';
  const find = selector => query(selector).find(visible);
  if (document.readyState === 'loading') return { ready: false };
  const host = location.hostname;
  const belongs = domain => host === domain || host.endsWith('.' + domain);
  if (isAuthHost(site, location.href)) {
    const input = find('input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=search]):not([type=submit]):not([type=button])');
    const text = document.body?.innerText || '';
    return { ready: !!input && (/^(email|tel|password)$/i.test(input.type) || /登录|扫码|sign\s*in|log\s*in|email/i.test(text)) };
  }
  let modal, selector;
  if (site === 'xiaohongshu' && belongs('xiaohongshu.com')) {
    modal = find('.login-modal, .login-container'); selector = 'button.login-btn';
  } else if (site === 'douyin' && belongs('douyin.com')) {
    modal = find('#login-panel-new, #douyin-login-new-id, #douyin_login_comp_flat_panel');
    selector = '#douyin-header button, #douyin-header #qNCGQxct';
  } else if (site === 'behance' && belongs('behance.net')) {
    const adobe = query('susi-sentry-light').find(node => node.shadowRoot &&
      [...node.shadowRoot.querySelectorAll('input:not([type=hidden]):not([type=search])')].some(visible));
    const form = adobe?.shadowRoot;
    if (form && /电子邮件|email/i.test(form.textContent || '') && /登录|sign\s*in|log\s*in/i.test(form.textContent || '') &&
        [...form.querySelectorAll('input:not([type=hidden]):not([type=search])')].some(visible)) return { ready: true };
    selector = 'button.adobeid-signin[data-signin-from="Header"], button.adobeid-signin[data-signin-from="updated_2020_navigation_login_hamburger"]';
  } else return { ready: false };
  if (modal && /登录|扫码|sign\s*in/i.test(modal.innerText || '')) return { ready: true };
  // SSR displays navigation buttons before deferred scripts bind their handlers.
  // DOMContentLoaded waits for those scripts, unlike readyState === interactive;
  // it does not wait for advertisements/images to finish loading.
  const timing = performance.getEntriesByType('navigation')[0];
  if (timing && !timing.domContentLoadedEventEnd) return { ready: false };
  const button = query(selector).find(node => visible(node) && !node.disabled &&
    node.getAttribute('aria-disabled') !== 'true' && !node.closest('form, [role=dialog], .login-modal, .login-container, #login-panel-new') &&
    /^(登录|登\s*录|log\s*in|sign\s*in)$/i.test(node.textContent.trim()));
  if (!button) return { ready: false };
  // Douyin's streamed HTML contains a visually complete placeholder button.
  // Its avatar is mounted by the client once account/login handling is ready.
  if (site === 'douyin' && button.tagName === 'BUTTON' && !button.querySelector('#svg_icon_avatar')) return { ready: false };
  const rect = button.getBoundingClientRect();
  const target = { x: Math.floor(rect.left + rect.width / 2), y: Math.floor(rect.top + rect.height / 2) };
  const hit = document.elementFromPoint(target.x, target.y);
  if (!hit || !button.contains(hit)) return { ready: false };
  if (!trigger) return { ready: false, canTrigger: true };
  // Return only the position of the verified navigation control. The main process
  // sends a real pointer event; button.click() omits pointer events used by sites.
  return { ready: false, target };
}

function visibleAuthFrames(site, isAuthHost) {
  const roots = [document], hosts = new Set();
  // Adobe embeds its current form inside an open shadow root. Hidden background
  // authentication frames must not count as a visible login window.
  for (let index = 0; index < roots.length && index < 100; index++) {
    const root = roots[index];
    for (const node of [...root.querySelectorAll('*')].slice(0, 8000)) {
      if (node.shadowRoot) roots.push(node.shadowRoot);
      if (node.tagName !== 'IFRAME' || !isAuthHost(site, node.src)) continue;
      const rect = node.getBoundingClientRect(), css = getComputedStyle(node);
      if (rect.width < 20 || rect.height < 20 || css.visibility === 'hidden' || css.display === 'none' || css.opacity === '0') continue;
      const left = Math.max(0, rect.left), right = Math.min(innerWidth, rect.right);
      const top = Math.max(0, rect.top), bottom = Math.min(innerHeight, rect.bottom);
      if (right - left < 20 || bottom - top < 20) continue;
      const x = Math.floor((left + right) / 2), y = Math.floor((top + bottom) / 2);
      if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) continue;
      let outer = node, parentRoot;
      while ((parentRoot = outer.getRootNode()).host) outer = parentRoot.host;
      if (document.elementFromPoint(x, y) !== outer || (root.elementFromPoint && root.elementFromPoint(x, y) !== node)) continue;
      hosts.add(new URL(node.src).hostname);
    }
  }
  return [...hosts];
}

function surfaceScript(site, trigger = false) {
  loginPlan(site);
  return `({...(${loginSurface.toString()})(${JSON.stringify(site)},${!!trigger},${loginHost.toString()}), frames: (${visibleAuthFrames.toString()})(${JSON.stringify(site)},${loginHost.toString()})})`;
}

async function waitForLogin({ probe, trigger, active, timeoutMs = 30000, intervalMs = 180 }) {
  const deadline = Date.now() + timeoutMs;
  let clicked = false;
  while (Date.now() < deadline) {
    if (!active()) throw Object.assign(new Error('已停止打开登录，请在当前站点重新点击登录'), { cancelled: true });
    const result = await probe();
    if (!active()) throw Object.assign(new Error('已停止打开登录，请在当前站点重新点击登录'), { cancelled: true });
    if (result?.ready) return;
    if (result?.canTrigger && !clicked) {
      clicked = true; // No automatic repeat, even if navigation destroys the response.
      await trigger();
    }
    await new Promise(resolve => setTimeout(resolve, intervalMs));
  }
  throw new Error('未能打开登录界面，请确认左侧网页已加载完成后重试，或导入 Cookies');
}

module.exports = { WEB_LOGIN, loginPlan, loginHost, surfaceScript, waitForLogin };
