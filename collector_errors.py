"""Bounded, redacted exception evidence for the desktop's error classifier."""
from __future__ import annotations
import errno
import re
from urllib.parse import urlsplit


def safe_error_text(value):
    text = re.sub(r'\x1b\[[0-9;]*m', '', str(value))
    def hide_url(match):
        try:
            url = urlsplit(match[0])
            return f'{url.scheme}://{url.hostname}' + (f':{url.port}' if url.port else '') + '/…'
        except ValueError:
            return '[链接]'
    text = re.sub(r'https?://[^\s<>"\']+', hide_url, text, flags=re.I)
    text = re.sub(r'\b(cookie|authorization|proxy-authorization|sessdata|web_session|sessionid|signature|token)\s*[:=]\s*[^\r\n]+', r'\1=[已隐藏]', text, flags=re.I)
    return text[:1800]


def error_payload(error):
    pending, seen, messages, result = [error], set(), [], {}
    while pending and len(seen) < 8:
        current = pending.pop(0)
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        if getattr(current, 'failure_code', None) == 'IMAGE_RESOLUTION_LIMITED':
            result['code'] = 'IMAGE_RESOLUTION_LIMITED'
        message = safe_error_text(current)
        if message and message not in messages:
            messages.append(message)
        if isinstance(current, TimeoutError):
            result.setdefault('kind', 'timeout')
        if isinstance(current, OSError) and ((current.errno is not None and getattr(current, 'filename', None)) or current.errno == errno.ENOSPC):
            result.setdefault('kind', 'filesystem')
        for status in (getattr(current, 'status', None), getattr(current, 'code', None), getattr(getattr(current, 'response', None), 'status', None)):
            if isinstance(status, int) and 400 <= status <= 599:
                result.setdefault('httpStatus', status)
        pending.extend([getattr(current, '__cause__', None), getattr(current, 'cause', None), getattr(current, 'reason', None)])
        info = getattr(current, 'exc_info', None)
        if isinstance(info, tuple) and len(info) > 1:
            pending.append(info[1])
        if not current.__suppress_context__:
            pending.append(current.__context__)
    result['message'] = '\n'.join(messages)[:1800]
    return result
