"""Native ZCOOL MP4s: bounded metadata probing and guarded yt-dlp downloads."""
from __future__ import annotations

import http.cookiejar
import io
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urljoin, urlparse

import requests


class ProtectedVideo(ValueError):
    pass


def media_url(value):
    parsed = urlparse(str(value))
    if (parsed.scheme not in {'http', 'https'} or parsed.hostname != 'video.zcool.cn'
            or parsed.username or parsed.password or parsed.port not in {None, 80, 443}
            or not parsed.path.lower().endswith('.mp4')):
        raise ValueError('当前仅支持页面提供的站酷原生 MP4 视频')
    return parsed._replace(scheme='https', netloc='video.zcool.cn', fragment='').geturl()


def video_rows(context):
    rows = context.get('videos') or []
    if not isinstance(rows, list) or len(rows) > 24:
        raise ValueError('站酷视频清单无效，请重新解析')
    result, seen = [], set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict): continue
        try: target = media_url(row.get('url'))
        except ValueError: continue
        key = str(row.get('id') or f'index-{index + 1}')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', key) or key in seen: continue
        seen.add(key)
        result.append({**row, 'url': target, 'mediaId': 'video-' + key})
    return result


class CheckedStream(io.RawIOBase):
    def __init__(self, response, cancel, prefix=b''):
        self.response, self.cancel, self.prefix = response, cancel, prefix

    def readable(self): return True

    def read(self, size=-1):
        self.cancel()
        if size is None or size < 0:
            value, self.prefix = self.prefix + self.response.raw.read(), b''
        else:
            start, self.prefix = self.prefix[:size], self.prefix[size:]
            value = start + self.response.raw.read(size - len(start)) if len(start) < size else start
        self.cancel()
        return value

    def close(self):
        if not self.closed: self.response.close()
        super().close()


class VideoSession:
    def __init__(self, collector):
        self.collector = collector
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({'Referer': 'https://www.zcool.com.cn/', 'Accept-Encoding': 'identity'})
        if collector.request.get('userAgent'):
            self.session.headers['User-Agent'] = collector.request['userAgent']
        proxy = (collector.request.get('settings') or {}).get('proxy')
        if proxy: self.session.proxies.update({'http': proxy, 'https': proxy})
        cookie = collector.request.get('cookieFile')
        if cookie:
            jar = http.cookiejar.MozillaCookieJar(cookie)
            jar.load(ignore_discard=True, ignore_expires=False)
            for value in jar:
                if value.domain.lstrip('.') in {'zcool.com.cn', 'www.zcool.com.cn', 'zcool.cn', 'video.zcool.cn'}:
                    self.session.cookies.set_cookie(value)

    def __enter__(self): return self
    def __exit__(self, *_args): self.session.close()

    def get(self, target, headers=None, timeout=10, deadline=None):
        for _ in range(5):
            self.collector.check_cancel()
            if deadline is not None:
                if time.monotonic() >= deadline: raise TimeoutError('站酷视频元数据查询超时')
                timeout = max(0.1, min(timeout, deadline - time.monotonic()))
            target = media_url(target)
            try:
                response = self.session.get(target, headers={**(headers or {}), 'Referer': 'https://www.zcool.com.cn/',
                    'Accept-Encoding': 'identity'}, stream=True, allow_redirects=False, timeout=(min(5, timeout), timeout))
            except requests.Timeout as error: raise TimeoutError('站酷视频请求超时') from error
            except requests.RequestException as error: raise ConnectionError('站酷视频网络连接失败') from error
            try: self.collector.check_cancel()
            except BaseException: response.close(); raise
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get('Location'); response.close()
                if not location: raise ValueError('站酷视频跳转地址为空')
                target = urljoin(target, location)
                continue
            return response
        raise ValueError('站酷视频地址跳转过多')

    def urlopen(self, request):
        from yt_dlp.networking import Response
        from yt_dlp.networking.exceptions import HTTPError
        if request.method != 'GET' or request.data:
            raise ValueError('站酷视频仅支持读取请求')
        response = self.get(request.url, dict(request.headers), timeout=15)
        if response.status_code not in {200, 206}:
            status, headers = response.status_code, dict(response.headers)
            response.close()
            wrapped = Response(io.BytesIO(), request.url, headers, status)
            raise HTTPError(wrapped)
        prefix = b''
        try:
            if not re.match(r'^(video/mp4|application/octet-stream)(?:;|$)', response.headers.get('Content-Type', ''), re.I):
                raise ValueError('服务器未返回 MP4 视频，请在左侧检查登录或验证后重新解析')
            start = re.match(r'bytes (\d+)-', response.headers.get('Content-Range', ''))
            if response.status_code == 200 or (start and start[1] == '0'):
                prefix = response.raw.read(32)
                if prefix[4:8] != b'ftyp': raise ValueError('服务器未返回有效的 MP4 视频内容')
            return Response(CheckedStream(response, self.collector.check_cancel, prefix), request.url,
                response.headers, response.status_code)
        except BaseException:
            response.close(); raise

    def probe(self, target):
        """Read at most 2 MiB of MP4 metadata, skipping mdat with validated ranges."""
        deadline, budget, total = time.monotonic() + 10, 2 * 1024 * 1024, None
        def read(offset, length):
            nonlocal budget, total
            self.collector.check_cancel()
            if length > budget or time.monotonic() >= deadline: raise ValueError('视频元数据查询已达到读取上限')
            with self.get(target, {'Range': f'bytes={offset}-{offset + length - 1}'},
                          timeout=max(0.1, min(5, deadline - time.monotonic())), deadline=deadline) as response:
                if response.status_code not in {200, 206}:
                    error = OSError(f'HTTP Error {response.status_code}: 站酷视频请求失败')
                    error.status = response.status_code
                    raise error
                if not re.match(r'^(video/mp4|application/octet-stream)(?:;|$)', response.headers.get('Content-Type', ''), re.I):
                    raise ValueError('站酷视频未返回媒体内容')
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                if response.status_code == 206:
                    if not match or int(match[1]) != offset: raise ValueError('视频元数据分段响应不匹配')
                    total = int(match[3])
                elif offset:
                    raise ValueError('视频服务器不支持分段读取，时长暂未知')
                elif response.headers.get('Content-Length', '').isdigit(): total = int(response.headers['Content-Length'])
                chunks, remaining = [], length
                while remaining:
                    self.collector.check_cancel()
                    if time.monotonic() >= deadline: raise TimeoutError('站酷视频元数据查询超时')
                    value = response.raw.read(min(16384, remaining))
                    if not value: break
                    chunks.append(value); remaining -= len(value); budget -= len(value)
                return b''.join(chunks)
        info = {}
        try:
            first = read(0, 256 * 1024)
            if first[4:8] != b'ftyp': raise ValueError('无效的 MP4 文件头')
            offset, ftyp = 0, b''
            for _ in range(16):
                header = first[offset:offset + 16] if offset + 16 <= len(first) else read(offset, 16)
                if len(header) < 8: break
                length, kind = int.from_bytes(header[:4], 'big'), header[4:8]
                if length == 1: length = int.from_bytes(header[8:16], 'big')
                if length < 8 or (total and offset + length > total): break
                if kind in {b'ftyp', b'moov'}:
                    if length > 1536 * 1024: break
                    box = first[offset:offset + length] if offset + length <= len(first) else read(offset, length)
                    if len(box) != length: break
                    if kind == b'ftyp': ftyp = box
                    else:
                        info.update(probe_boxes(ftyp + box, self.collector))
                        break
                offset += length
                if total and offset >= total: break
        except Exception as error:
            self.collector.check_cancel()
            if isinstance(error, ProtectedVideo) or getattr(error, 'status', 0) in {401, 403, 404, 410}: raise
            self.collector.log('暂未取得视频完整参数，仍可尝试下载；时长或大小显示未知。')
        if total and total > 0: info['filesize'] = total
        return info


def probe_boxes(data, collector):
    executable = shutil.which('ffprobe')
    if not executable: return {}
    with tempfile.TemporaryDirectory(prefix='media-dl-zcool-') as name:
        file = Path(name) / 'metadata.mp4'; file.write_bytes(data)
        collector.check_cancel()
        response = subprocess.run([executable, '-v', 'quiet', '-protocol_whitelist', 'file', '-enable_drefs', '0', '-f', 'mov',
            '-show_entries', 'format=duration:stream=codec_type,codec_name,codec_tag_string,width,height,avg_frame_rate',
            '-of', 'json', str(file)], capture_output=True, timeout=4, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        collector.check_cancel()
        value = json.loads(response.stdout)
    streams = value.get('streams') or []
    if any(row.get('codec_tag_string') in {'encv', 'enca'} for row in streams):
        raise ProtectedVideo('此视频受加密保护，暂不支持下载')
    video = next((row for row in streams if row.get('codec_type') == 'video'), {})
    audio = next((row for row in streams if row.get('codec_type') == 'audio'), {})
    duration = float((value.get('format') or {}).get('duration') or 0)
    result = {'width': video.get('width'), 'height': video.get('height'), 'vcodec': video.get('codec_name') or 'unknown',
        'acodec': audio.get('codec_name') if audio else 'none'}
    if math.isfinite(duration) and duration > 0: result['duration'] = duration
    return result


def video_info(collector, url, row, index, metadata=None):
    context = collector.request['context']; metadata = metadata or {}
    title = f"{str(context.get('title') or context['workId'])[:200]} · 视频 {index}"
    fmt = {'format_id': 'mp4', 'url': row['url'], 'ext': 'mp4', 'protocol': 'https',
        'vcodec': metadata.get('vcodec') or 'unknown', 'acodec': metadata.get('acodec') or 'unknown',
        'width': metadata.get('width'), 'height': metadata.get('height'),
        'filesize': metadata.get('filesize'), 'format_note': '页面提供的 MP4',
        'http_headers': {'Referer': 'https://www.zcool.com.cn/'}}
    cover = row.get('cover')
    try:
        parsed = urlparse(str(cover or ''))
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in {None, 443} or
                not any((parsed.hostname or '') == host or (parsed.hostname or '').endswith('.' + host) for host in ('zcool.cn', 'zcool.com.cn'))): cover = None
    except ValueError: cover = None
    if not cover:
        from zcool_adapter import image_url
        for image in context.get('images') or []:
            try: cover = image_url(image.get('urlSmall') or image.get('urlBig') or image.get('url')); break
            except (ValueError, AttributeError): pass
    return {'id': context['workId'] + '_' + row['mediaId'], 'title': title, 'uploader': context.get('author') or '',
        'duration': metadata.get('duration'), 'thumbnail': cover, 'webpage_url': url,
        'extractor': 'zcool:browser', 'extractor_key': 'Zcool', 'formats': [fmt]}


def inspect_videos(collector, url):
    from collector_core import normalise_video
    items = []
    for index, row in enumerate(video_rows(collector.request['context']), 1):
        collector.check_cancel()
        try:
            with VideoSession(collector) as session: meta = session.probe(row['url'])
            item = normalise_video(video_info(collector, url, row, index, meta), url)
            item['mediaId'] = row['mediaId']
            items.append(item)
        except Exception as error:
            collector.check_cancel()
            from collector_errors import safe_error_text
            collector.log('站酷视频参数查询失败：' + safe_error_text(error))
            items.append({'id': row['mediaId'], 'kind': 'unavailable', 'url': url,
                'mediaId': row['mediaId'], 'title': f"{collector.request['context'].get('title') or '站酷作品'} · 视频 {index}",
                'message': str(error) if isinstance(error, ProtectedVideo) else '视频暂不可访问，请在左侧检查登录或网站验证后重新解析。'})
    return items


def download_video(collector, url, folder):
    import yt_dlp
    rows = video_rows(collector.request['context'])
    entry = next(((i, row) for i, row in enumerate(rows, 1) if row['mediaId'] == collector.request.get('mediaId')), None)
    if not entry: raise ValueError('站酷视频已变化，请重新解析')
    if collector.request.get('formatId') not in {None, '', 'mp4'}: raise ValueError('该画质已失效，请重新解析')
    index, row = entry
    options = collector.options(url)
    options.pop('cookiefile', None)
    if not (collector.request.get('settings') or {}).get('audioOnly'): options['format'] = 'mp4'
    options.update(retries=2, socket_timeout=15)
    collector.log(f'正在下载站酷视频 {index}，使用网页实际提供的 MP4。')
    with VideoSession(collector) as session, yt_dlp.YoutubeDL(options) as ydl:
        info = video_info(collector, url, row, index, session.probe(row['url']))
        ydl.urlopen = session.urlopen
        ydl.process_ie_result(info, download=True)
    collector.check_cancel()
    return {'folder': str(folder), 'count': 1}
