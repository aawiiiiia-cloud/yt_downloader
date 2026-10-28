'use strict';
const REASONS = {
  CANCELLED: ['操作已取消', '操作已取消。'],
  AUTH_EXPIRED: ['登录凭证失效', '登录凭证已失效/过期，请重新登录。'],
  AUTH_REQUIRED: ['需要登录', '未检测到可用的登录凭证，请先登录后重试。'],
  NETWORK_TIMEOUT: ['网络超时', '网络连接超时，请检查网络或代理后重试。'],
  NETWORK_ERROR: ['网络连接失败', '网络连接失败，请检查网络或代理后重试。'],
  RATE_LIMITED: ['网站限流', '请求过于频繁，已被网站限流，请稍后重试。'],
  VERIFICATION_REQUIRED: ['需要网站验证', '网站要求验证，请先在左侧完成验证后重试。'],
  CONTENT_DELETED: ['视频已删除', '视频已删除或下架，无法下载。'],
  CONTENT_UNAVAILABLE: ['内容不可用', '内容暂不可用或链接已失效，请在左侧确认后重新解析。'],
  ACCESS_DENIED: ['访问被拒绝', '网站拒绝访问，请确认账号权限或网站的访问限制。'],
  LINK_EXPIRED: ['下载链接失效', '下载链接已失效，请重新解析当前页面后再下载。'],
  FORMAT_UNAVAILABLE: ['画质不可用', '所选画质暂不可用，请重新解析并选择其他画质。'],
  IMAGE_RESOLUTION_LIMITED: ['图片清晰度受限', '图片实际尺寸低于作品标注尺寸，未保存。请在左侧登录后重新解析，并确认网站提供的清晰度。'],
  TOOL_MISSING: ['合并或转换工具缺失', '缺少视频合并或转换工具，请到设置检查。'],
  SAVE_FAILED: ['文件保存失败', '文件保存失败，请检查保存位置、磁盘空间或写入权限。'],
  SERVER_ERROR: ['网站服务异常', '网站服务暂时异常，请稍后重试。'],
  INTERRUPTED: ['任务中断', '上次任务已中断，请重新解析或重试。'],
  UNKNOWN: ['原因未确定', '下载失败，暂时无法确定原因，请展开任务日志查看详情。'],
};
function safeDetail(value) {
  return String(value || '').replace(/\u001b\[[0-9;]*m/g, '')
    .replace(/https?:\/\/[^\s<>"']+/gi, value => {
      try { const url = new URL(value); return `${url.protocol}//${url.host}/…`; } catch { return '[链接]'; }
    })
    .replace(/\b(cookie|authorization|proxy-authorization|sessdata|web_session|sessionid|signature|token)\s*[:=]\s*[^\r\n]+/gi, '$1=[已隐藏]')
    .slice(0, 1800);
}
function describeFailure(code, detail = '') {
  if (!Object.hasOwn(REASONS, code)) code = 'UNKNOWN';
  const [label, message] = REASONS[code];
  return { code, label, message, detail: safeDetail(detail) };
}
function classifyFailure(error, logs = [], { hadCredentials = false } = {}) {
  const data = error?.failure || error || {};
  let text = String(data.detail || data.message || (typeof error === 'string' ? error : ''));
  let code = data.code || error?.code;
  if (error?.cancelled || code === 'CANCELLED' || /操作已取消|用户取消|cancel[le]+d by (?:the )?user/i.test(text)) return describeFailure('CANCELLED', text);
  if (!Object.hasOwn(REASONS, code) || code === 'UNKNOWN') {
    // Only terminal error lines may refine a generic failure. Earlier warnings
    // can describe a recoverable problem which did not cause this failure.
    const terminal = [...logs].reverse().find(line => /^\s*(?:ERROR:|错误[:：])/i.test(String(line)));
    const generic = /下载未完成|原因未确定|暂时无法确定|^下载失败[。.!]?$/.test(text);
    if (generic && terminal) text += '\n' + terminal;
    const status = Number(data.httpStatus) || Number(text.match(/(?:HTTP(?: Error|Error)?[:\s]+|status(?: code)?[:\s]+)([45]\d\d)\b/i)?.[1]);
    if (data.kind === 'filesystem') code = 'SAVE_FAILED';
    else if (status === 429 || /too many requests|rate[ -]?limit|请求过于频繁|访问过于频繁|操作过于频繁|被.{0,6}限流/i.test(text)) code = 'RATE_LIMITED';
    else if (status >= 500 && status <= 599) code = 'SERVER_ERROR';
    else if (data.kind === 'timeout' || ['ETIMEDOUT', 'ESOCKETTIMEDOUT'].includes(code) || /timed?[_ -]?out|timeout|超时/i.test(text)) code = 'NETWORK_TIMEOUT';
    else if (/captcha|verify (?:that )?you(?:['’]re| are) (?:a )?human|confirm you(?:['’]re| are) not a bot|人机验证|安全验证|验证码|风控|fresh cookies \(not necessarily logged in\)/i.test(text)) code = 'VERIFICATION_REQUIRED';
    else if (/(?:url|link|signature|request) (?:has |have |is )?expired|expired (?:url|link|signature)|(?:链接|签名)已?(?:失效|过期)/i.test(text)) code = 'LINK_EXPIRED';
    else if (/cookies? (?:are |is |have |has )?(?:no longer valid|expired|invalid)|account cookies have been rotated|(?:login|authenticated) session (?:has |is )?expired|(?:登录|认证)(?:凭证|状态|会话)?(?:已|已经)?(?:失效|过期)|凭证已失效\/过期/i.test(text)) code = 'AUTH_EXPIRED';
    else if (/(?:video|content) (?:has been |was |is )?(?:deleted|removed|taken down)|deleted (?:by|video)|(?:视频|稿件|内容)已(?:被)?(?:删除|下架)/i.test(text)) code = 'CONTENT_DELETED';
    else if (/private video|members?[ -]only|subscriber[ -]only|available to members|premium[ -]only|purchase (?:this|the) video|not available in your country|geo[ -]?restrict|confirm your age|age[ -]?restrict|无(?:权|访问权限)|权限不足|仅.{0,8}会员|私人视频|私密视频|地区限制/i.test(text)) code = 'ACCESS_DENIED';
    else if (code === 'YOUTUBE_AUTH_REQUIRED' || /(?:login|authentication|sign[ -]?in) (?:is )?required|not logged in|sign in to (?:download|watch|view)|(?:请先|需要|必须)(?:通过 Edge )?登录|请先通过 Edge 登录并验证/i.test(text)) code = 'AUTH_REQUIRED';
    else if (/no space left|not enough (?:disk )?space|磁盘空间不足|磁盘已满|无法写入|文件保存失败/i.test(text)) code = 'SAVE_FAILED';
    else if (['ECONNRESET', 'ECONNREFUSED', 'ENOTFOUND', 'EAI_AGAIN', 'ENETUNREACH', 'EHOSTUNREACH'].includes(code) || /connection (?:reset|refused|closed|aborted)|name (?:or service not known|resolution)|resolve (?:host|hostname)|getaddrinfo|proxy error|certificate_verify_failed|network (?:unavailable|unreachable)|ERR_NAME_NOT_RESOLVED|ERR_CONNECTION_(?:RESET|REFUSED|CLOSED)|ERR_PROXY_CONNECTION_FAILED|网络连接失败|代理连接失败/i.test(text)) code = 'NETWORK_ERROR';
    else if (/ffmpeg.{0,60}(?:not found|not installed|missing)|ffprobe.{0,60}(?:not found|not installed|missing)|未找到.{0,12}ffmpeg/i.test(text)) code = 'TOOL_MISSING';
    else if (/requested format (?:is )?not available|所选画质.{0,8}不可用/i.test(text)) code = 'FORMAT_UNAVAILABLE';
    else if (status === 401 || status === 403 || /HTTP Error 403|access denied|forbidden/i.test(text)) code = 'ACCESS_DENIED';
    else if ([404, 410].includes(status) || /video (?:is )?unavailable|video (?:does not exist|not found)|内容不存在|视频不可用|稿件不存在/i.test(text)) code = 'CONTENT_UNAVAILABLE';
    else if (/上次任务已中断/.test(text)) code = 'INTERRUPTED';
    else code = 'UNKNOWN';
  }
  if (code === 'AUTH_REQUIRED' && hadCredentials) code = 'AUTH_EXPIRED';
  return describeFailure(code, text);
}
function failureError(payload, { cancelled = false, operation } = {}) {
  const failure = classifyFailure(typeof payload === 'string' ? { message: payload, cancelled } : { ...payload, cancelled });
  return Object.assign(new Error(operation === 'download' || cancelled ? failure.message : failure.detail || failure.message), {
    code: failure.code, failure, cancelled: cancelled || failure.code === 'CANCELLED',
  });
}
module.exports = { REASONS, safeDetail, describeFailure, classifyFailure, failureError };
