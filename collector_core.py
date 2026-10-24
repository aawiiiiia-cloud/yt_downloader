"""Headless site adapters shared by Electron's short-lived Python tasks."""
from __future__ import annotations

import importlib.metadata
import http.cookiejar
import re
import shutil
import threading
import time
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode

from download_core import build_download_options, site_for_url, youtube_single_video


class Cancelled(RuntimeError):
    pass


def checked_url(value: str) -> str:
    value = str(value).strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or parsed.username or parsed.password or not site_for_url(value):
        raise ValueError("目前支持 YouTube、B站、小红书、Vimeo 和抖音的内容链接")
    if site_for_url(value) == 'douyin':
        video_id = parse_qs(parsed.query).get('modal_id', [''])[0]
        if not video_id and (match := re.search(r'/(?:video|share/video)/(\d+)', parsed.path)):
            video_id = match[1]
        if re.fullmatch(r'\d+', video_id):
            return f'https://www.douyin.com/video/{video_id}'
        if parsed.path.startswith('/note/'):
            raise ValueError('目前支持抖音视频，抖音图集暂未接入')
    return value


def vimeo_player_url(url: str) -> str:
    """Use Vimeo's public embedded player, retaining unlisted video access hashes."""
    if site_for_url(url) != 'vimeo':
        return url
    parsed = urlparse(url)
    match = re.fullmatch(r'/(?:video/|channels/[^/]+/|groups/[^/]+/videos/|ondemand/[^/]+/)?(\d+)(?:/([\da-f]+))?/?', parsed.path, re.I)
    if not match:
        return url
    access = match[2] or parse_qs(parsed.query).get('h', [''])[0]
    return f'https://player.vimeo.com/video/{match[1]}' + ('?' + urlencode({'h': access}) if access else '')


def selected_video_format(url: str, identifier: str, has_audio: bool) -> str:
    if has_audio:
        return identifier
    if site_for_url(url) == 'vimeo' and (match := re.fullmatch(r'(hls-.+)-\d+', identifier)):
        # Prefer audio delivered by the same CDN as the selected video, retaining fallback.
        return f'{identifier}+bestaudio[format_id^={match[1]}-audio]/{identifier}+bestaudio/{identifier}'
    return f'{identifier}+bestaudio/{identifier}'


def prepare_runtime() -> None:
    import bootstrap
    bootstrap._reuse_project_tools(lambda _message: None)
    for folder in (bootstrap.NODE_DIR, bootstrap.FFMPEG_DIR):
        if folder.is_dir():
            bootstrap._add_to_path(folder)


def runtime_health() -> dict:
    prepare_runtime()
    from pot_provider import find_provider
    packages = {}
    for name in ("yt-dlp", "yt-dlp-ejs", "Pillow", "pillow-heif", "dhash"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {"packages": packages, "ffmpeg": bool(shutil.which("ffmpeg")),
            "ffprobe": bool(shutil.which("ffprobe")), "node": bool(shutil.which("node")),
            "provider": find_provider() is not None}


def normalise_video(info: dict, source_url: str) -> dict:
    formats = []
    for item in info.get("formats") or []:
        identifier = str(item.get("format_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", identifier) or not item.get("url"):
            continue
        if item.get("has_drm"):
            continue
        video = item.get("vcodec") != "none"
        # HLS audio-only tracks may omit the codec name while explicitly excluding video.
        audio = item.get("acodec") != "none" and (item.get("acodec") is not None or not video)
        if not video and not audio:
            continue
        formats.append({"id": identifier, "height": item.get("height"), "width": item.get("width"),
                        "fps": item.get("fps"), "ext": item.get("ext"), "video": video,
                        "audio": audio, "size": item.get("filesize") or item.get("filesize_approx"),
                        "sizeEstimated": not bool(item.get("filesize")), "bitrate": item.get("tbr"),
                        "note": str(item.get("format_note") or "")[:100]})
    return {"id": str(info.get("id") or ""), "kind": "video", "url": source_url,
            "title": str(info.get("title") or "未命名视频"), "thumbnail": info.get("thumbnail") or next(
                (thumb.get('url') for thumb in reversed(info.get('thumbnails') or []) if thumb.get('url')), None),
            "author": info.get("uploader") or info.get("channel") or "",
            "duration": info.get("duration"), "formats": formats,
            "subtitles": list((info.get("subtitles") or {}).keys()),
            "parts": info.get("parts") or [], "currentPart": info.get("currentPart", 1)}


def browser_post(url: str, context: dict | None):
    """Accept only the current note, never a note left over from another SPA route."""
    from xhs_image_downloader import post_from_initial_state
    match = re.search(r"/(?:explore|discovery/item|user/profile/[^/]+)/([0-9a-f]{16,32})", url, re.I)
    if not match or not context or str(context.get("noteId", "")).lower() != match[1].lower():
        return None
    note = context.get("note")
    if not isinstance(note, dict):
        return None
    post = post_from_initial_state({"note": {"noteDetailMap": {match[1]: {"note": note}}}}, match[1], url)
    def image_url(candidate):
        parsed = urlparse(candidate)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.port not in {None, 80, 443} or not any(
            host == domain or host.endswith("." + domain)
            for domain in ("xhscdn.com", "xiaohongshu.com", "xhsimg.com")
        ):
            raise ValueError(f"笔记图片地址不属于受支持的素材域名（{parsed.scheme}://{host}），请刷新后重新解析")
        # Real note state still contains HTTP display URLs; fetch trusted CDNs over HTTPS.
        return parsed._replace(scheme="https", netloc=host).geturl()

    return replace(post, images=tuple(replace(image, url=image_url(image.url),
        fallback_urls=tuple(image_url(candidate) for candidate in image.fallback_urls)) for image in post.images))


def browser_bilibili(url: str, context: dict | None, ydl):
    """Use the actual browser's player response when the site's HTML differs for HTTP clients."""
    from yt_dlp.extractor.bilibili import BiliBiliIE
    match = re.search(r"/video/(BV[0-9A-Za-z]+)", url)
    if not match or not context or context.get("videoId") != match[1]:
        return None
    part = int(parse_qs(urlparse(url).query).get("p", ["1"])[0])
    if context.get("part", 1) != part:
        return None
    play_info = context.get("playInfo") or {}
    if not isinstance(play_info, dict):
        return None
    formats = BiliBiliIE(ydl).extract_formats(play_info)
    if not formats:
        return None
    for item in formats:
        parsed = urlparse(str(item.get("url") or ""))
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"https", "http"} or not any(host == domain or host.endswith('.' + domain)
            for domain in ("bilivideo.com", "bilivideo.cn", "bilibili.com", "akamaized.net", "biliapi.net")):
            raise ValueError("播放器资源地址不属于受支持的 B站素材域名，请刷新重试")
    video = context.get("video") or {}
    parts = []
    for page in (video.get("pages") or [])[:200]:
        number = page.get("page")
        if not isinstance(number, int) or number < 1:
            continue
        parsed_url = urlparse(url)
        query = parse_qs(parsed_url.query); query['p'] = [str(number)]
        parts.append({"index": number, "title": str(page.get("part") or f'P{number}'),
                      "url": parsed_url._replace(query=urlencode(query, doseq=True)).geturl()})
    return {"id": match[1] + f"_p{part}", "title": video.get("title") or match[1],
            "extractor": "BiliBili", "extractor_key": "BiliBili",
            "thumbnail": video.get("pic"), "uploader": video.get("owner", {}).get("name", ""),
            "duration": next((page.get('duration') for page in video.get('pages', []) if page.get('page') == part and page.get('duration')), video.get('duration')),
            "formats": formats, "webpage_url": url,
            "parts": parts, "currentPart": part,
            "http_headers": {"Referer": "https://www.bilibili.com/", "Origin": "https://www.bilibili.com"}}


def browser_douyin(url: str, context: dict | None, ydl):
    """Parse only matching video data the normal browser has already received."""
    from yt_dlp.extractor.tiktok import DouyinIE
    match = re.search(r'/video/(\d+)', urlparse(url).path)
    if not match or not isinstance(context, dict) or str(context.get('videoId')) != match[1]:
        return None
    detail = context.get('detail') or {}
    if not isinstance(detail, dict) or str(detail.get('aweme_id')) != match[1]:
        return None
    raw = detail.get('video')
    if not isinstance(raw, dict):
        return None
    media_domains = ('douyinvod.com', 'zjcdn.com', 'douyin.com', 'amemv.com', 'snssdk.com', 'bytecdn.cn')
    image_domains = ('douyinpic.com', 'douyincdn.com', 'byteimg.com', 'ibytedtos.com', 'pstatp.com')

    def trusted(value, domains):
        parsed = urlparse('https:' + value if value.startswith('//') else value)
        host = (parsed.hostname or '').lower()
        if parsed.scheme not in {'https', 'http'} or parsed.username or parsed.password or parsed.port not in {None, 80, 443} or not any(
            host == domain or host.endswith('.' + domain) for domain in domains
        ):
            raise ValueError('抖音播放器资源地址不属于支持的素材域名，请刷新后重新解析')
        return parsed._replace(scheme='https', netloc=host).geturl()

    def address(value, domains=media_domains):
        if isinstance(value, dict):
            urls = value.get('url_list') or value.get('urlList') or ([value.get('src')] if value.get('src') else [])
            meta = {key: value[key] for key in ('width', 'height', 'data_size', 'url_key') if value.get(key) is not None}
        else:
            urls, meta = ([value] if isinstance(value, str) else value or []), {}
        if not isinstance(urls, (list, tuple)):
            urls = [urls] if isinstance(urls, str) else []
        candidates = []
        for row in list(urls)[:8]:
            candidate = row.get('src') or row.get('url') if isinstance(row, dict) else row
            if isinstance(candidate, str) and candidate:
                candidates.append(trusted(candidate, domains))
        return {**meta, 'url_list': candidates}

    video = {key: raw[key] for key in ('width', 'height', 'duration', 'is_h265', 'is_bytevc1') if key in raw}
    for snake, camel in (('play_addr', 'playAddr'), ('play_addr_h264', 'playAddrH264'),
                         ('play_addr_bytevc1', 'playAddrBytevc1'), ('download_addr', 'downloadAddr')):
        if value := raw.get(snake) or raw.get(camel):
            video[snake] = address(value)
    for snake, camel in (('cover', 'cover'), ('origin_cover', 'originCover')):
        if value := raw.get(snake) or raw.get(camel):
            video[snake] = address(value, image_domains)
    video['bit_rate'] = []
    format_metadata = {}
    for index, row in enumerate((raw.get('bit_rate') or raw.get('bitRateList') or [])[:100]):
        # Browser DASH rows are video-only byte ranges; a complete MP4 is required here.
        if isinstance(row, dict) and str(row.get('format') or row.get('videoFormat') or '').lower() == 'dash':
            continue
        if isinstance(row, dict) and (value := row.get('play_addr') or row.get('playAddr')):
            addr = address(value)
            metadata = {key: row[key] for key in ('width', 'height') if row.get(key)}
            metadata.update({key: val for key, val in {
                'filesize': row.get('data_size') or row.get('dataSize') or addr.get('data_size'),
                'fps': row.get('FPS') or row.get('fps'),
                'vcodec': 'h265' if row.get('is_bytevc1') or row.get('isBytevc1') or row.get('isH265') or row.get('is_h265') else 'h264',
            }.items() if val is not None})
            for stream in addr['url_list']:
                format_metadata[stream] = metadata
            video['bit_rate'].append({'gear_name': row.get('gear_name') or row.get('gearName') or f'browser-{index}',
                                      'bit_rate': row.get('bit_rate') or row.get('bitRate'),
                                      'is_bytevc1': row.get('is_bytevc1') or row.get('isBytevc1') or row.get('isH265'),
                                      'FPS': row.get('FPS') or row.get('fps'), 'play_addr': addr})
    info = DouyinIE(ydl)._parse_aweme_video_app({'aweme_id': match[1], 'desc': detail.get('desc') or '',
                                                'author': detail.get('author') or {}, 'video': video})
    if not info.get('formats'):
        return None
    for entry in info['formats']:
        entry.update(format_metadata.get(entry['url'], {}))
    info.update(extractor='Douyin', extractor_key='Douyin', webpage_url=url,
                http_headers={'Referer': 'https://www.douyin.com/'})
    for cover in ('origin_cover', 'cover'):
        if urls := (video.get(cover) or {}).get('url_list'):
            info['thumbnail'] = urls[0]; break
    author = detail.get('author') or {}
    info['uploader'] = author.get('nickname') or info.get('uploader')
    return info


class Collector:
    def __init__(self, request: dict, emit, cancel: threading.Event | None = None):
        self.request, self.emit = request, emit
        self.cancel = cancel or threading.Event()
        self.provider = None
        self.reported_formats = set()

    def check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled("操作已取消")

    def log(self, message):
        self.check_cancel()
        # Logs must never become a cookie/header dump or retain signed URL tokens.
        message = re.sub(r"(?i)(xsec_token|token|signature|sig|h|a_bogus|x-bogus|mstoken)=([^&\s]+)", r"\1=…", str(message))
        self.emit("log", message[:1600])

    def options(self, url: str) -> dict:
        settings = self.request.get("settings") or {}
        options = build_download_options(
            str(settings.get("saveDir") or Path.home() / "Downloads"),
            height="audio" if settings.get("audioOnly") else None,
            format_name="mp3" if settings.get("audioOnly") else "mp4",
            subtitles=bool(settings.get("subtitles")), proxy=str(settings.get("proxy") or ""),
            js_runtime="node" if shutil.which("node") else ("deno" if shutil.which("deno") else None),
            logger=self, progress_hooks=[self.progress], postprocessor_hooks=[self.postprocess],
        )
        cookie = self.request.get("cookieFile")
        if cookie and Path(cookie).is_file():
            options["cookiefile"] = str(cookie)
        user_agent = self.request.get("userAgent")
        if user_agent:
            options["http_headers"] = {"User-Agent": str(user_agent)}
        options["playlistend"] = 50
        if youtube_single_video(url):
            options["noplaylist"] = True
        if site_for_url(url) == "youtube":
            from pot_provider import PotProviderManager
            self.provider = PotProviderManager()
            if self.provider.ensure_started(self.log, self.cancel, timeout=20):
                self.provider.apply_to_opts(options)
            else:
                self.log("PO Token 服务未启用，当前可用画质以实际解析结果为准")
        return options

    def debug(self, message):
        if site_for_url(str(self.request.get('url') or '')) == 'douyin' and 'fresh cookies' in str(message).lower():
            raise RuntimeError('抖音尚未返回可下载的视频信息。请先在左侧完成页面加载、必要的验证或登录，再重新解析；也可导入近期导出的 Cookies。')
        if self.request.get('youtubeVerified') and any(marker in str(message).lower() for marker in (
            'cookies are no longer valid', 'account cookies have been rotated', 'cookies have expired',
        )):
            self.emit('auth-invalid', 'YouTube 登录已失效，请通过 Edge 重新登录')
            raise RuntimeError('YouTube 登录已失效，请通过 Edge 重新登录')
        if not str(message).startswith("[debug]"):
            self.log(message)

    info = debug
    warning = debug
    error = debug

    def progress(self, data):
        self.check_cancel()
        total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
        info = data.get("info_dict") or {}
        identifier = info.get("format_id")
        if identifier and identifier not in self.reported_formats:
            self.reported_formats.add(identifier)
            self.log(f"[格式] {identifier} · {info.get('width', '?')}×{info.get('height', '?')} · {info.get('fps', '?')}fps")
        self.emit("progress", {"percent": min(100, data.get("downloaded_bytes", 0) / total * 100) if total else 0,
                               "status": data.get("status"), "speed": data.get("speed"), "eta": data.get("eta"),
                               "height": info.get("height"), "formatId": info.get("format_id")})

    def postprocess(self, _data):
        self.check_cancel()
        self.emit("progress", {"status": "processing", "percent": 100})

    def xhs_downloader(self):
        from xhs_image_downloader import XhsImageDownloader
        settings = self.request.get("settings") or {}
        return XhsImageDownloader(
            cookie_file=Path(self.request["cookieFile"]) if self.request.get("cookieFile") else None,
            proxy=str(settings.get("proxy") or ""), progress=self.log, cancel_event=self.cancel,
            image_format=settings.get("imageFormat", "original"),
            image_quality=int(settings.get("imageQuality", 95)),
            user_agent=self.request.get("userAgent"),
            item_progress=lambda current, total, name: self.emit("progress", {
                "status": "downloading", "percent": current / max(total, 1) * 100, "name": name,
            }),
        )

    def inspect(self) -> dict:
        import yt_dlp
        from xhs_image_downloader import XhsVideoPost
        url = checked_url(self.request["url"])
        self.check_cancel()
        if site_for_url(url) == "xiaohongshu":
            try:
                post = browser_post(url, self.request.get("context")) or self.xhs_downloader().inspect(url)
                return {"site": "xiaohongshu", "url": post.source_url, "items": [{
                    "id": post.note_id, "kind": "images", "title": post.title, "author": post.author,
                    "url": post.source_url, "description": post.description,
                    "thumbnail": post.images[0].url, "images": [asdict(image) for image in post.images],
                }]}
            except XhsVideoPost:
                self.log("已识别为小红书视频，使用视频解析器")
        options = self.options(url)
        options.update(retries=2, socket_timeout=20)
        with yt_dlp.YoutubeDL(options) as ydl:
            if site_for_url(url) == "bilibili":
                loaded = browser_bilibili(url, self.request.get("context"), ydl)
                if loaded:
                    loaded = ydl.process_ie_result(loaded, download=False)
                    return {"site": "bilibili", "url": url, "items": [normalise_video(loaded, url)]}
            if site_for_url(url) == 'douyin':
                loaded = browser_douyin(url, self.request.get('context'), ydl)
                if loaded:
                    loaded = ydl.process_ie_result(loaded, download=False)
                    return {'site': 'douyin', 'url': url, 'items': [normalise_video(loaded, url)]}
            data = ydl.extract_info(vimeo_player_url(url), download=False)
        self.check_cancel()
        if not data:
            raise RuntimeError("未取得可下载内容，请检查登录状态或刷新页面")
        entries = data.get("entries")
        items = []
        if entries is not None:
            for entry in entries:
                if entry:
                    entry_url = entry.get("webpage_url") or entry.get("original_url") or url
                    items.append(normalise_video(entry, checked_url(entry_url)))
                if len(items) >= 50:
                    break
        else:
            items.append(normalise_video(data, url))
        if not items:
            raise RuntimeError("当前页面没有可解析的视频条目")
        if not any(item.get("formats") for item in items):
            raise RuntimeError("尚未取得可下载画质，请等待左侧播放器加载，或登录后重新解析")
        return {"site": site_for_url(url), "url": url, "items": items}

    def download(self) -> dict:
        import yt_dlp
        from xhs_image_downloader import XhsVideoPost
        url = checked_url(self.request["url"])
        settings = self.request.get("settings") or {}
        if site_for_url(url) == 'youtube':
            jar = http.cookiejar.MozillaCookieJar()
            try:
                jar.load(self.request.get('cookieFile') or '', ignore_discard=True, ignore_expires=True)
            except (OSError, http.cookiejar.LoadError):
                pass
            auth = {'SID', 'SAPISID', 'LOGIN_INFO', '__Secure-3PAPISID', '__Secure-1PSID'}
            valid = any(cookie.value and cookie.name in auth and
                (cookie.domain.lstrip('.') == 'youtube.com' or cookie.domain.endswith('.youtube.com')) and
                (not cookie.expires or cookie.expires > time.time()) for cookie in jar)
            if not self.request.get('youtubeVerified') or not valid:
                raise ValueError('请先通过 Edge 登录并验证 YouTube，再下载视频')
        folder = Path(settings["saveDir"]).expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=True)
        self.check_cancel()
        if site_for_url(url) == "xiaohongshu" and self.request.get("kind") == "images":
            try:
                downloader = self.xhs_downloader()
                post = browser_post(url, self.request.get("context")) or downloader.inspect(url)
                selected = set(self.request.get("indices") or [])
                images = tuple(image for image in post.images if image.index in selected)
                if not images:
                    raise ValueError("请至少选择一张图片")
                _post, paths = downloader.download_post(replace(post, images=images), folder)
                return {"count": len(paths), "folder": str(paths[0].parent)}
            except XhsVideoPost as exc:
                raise ValueError("笔记内容已变化，请重新解析") from exc
        options = self.options(url)
        identifier = str(self.request.get("formatId") or "")
        if identifier and not settings.get("audioOnly"):
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", identifier):
                raise ValueError("画质标识无效，请重新解析")
            options["format"] = selected_video_format(url, identifier, bool(self.request.get('formatHasAudio')))
        with yt_dlp.YoutubeDL(options) as ydl:
            loaded = browser_bilibili(url, self.request.get("context"), ydl) if site_for_url(url) == 'bilibili' else (
                browser_douyin(url, self.request.get('context'), ydl) if site_for_url(url) == 'douyin' else None)
            if loaded:
                ydl.process_ie_result(loaded, download=True)
                result = 0
            else:
                result = ydl.download([vimeo_player_url(url)])
            if result:
                raise RuntimeError("下载未完成，请查看任务日志后重试")
        self.check_cancel()
        return {"folder": str(folder), "count": 1}

    def close(self):
        if self.provider is not None:
            self.provider.stop()
