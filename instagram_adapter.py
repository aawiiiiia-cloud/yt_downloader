"""Instagram parsing via gallery-dl; verified image and yt-dlp video writers."""
from __future__ import annotations

import copy
import http.cookiejar
import math
import re
import time
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

from xhs_image_downloader import XhsImage, XhsPost, XhsImageDownloader
from image_summary import write_image_summary

CDN_DOMAINS = ('cdninstagram.com', 'fbcdn.net')


def post_url(value):
    parsed = urlparse(value)
    match = re.fullmatch(
        r'/(?:(?!accounts/|stories/|explore/|direct/|share/|challenge/|checkpoint/)'
        r'[A-Za-z0-9._]{1,30}/)?(?:p|reels?)/([A-Za-z0-9_-]{5,32})/?', parsed.path)
    if (parsed.scheme not in ('http', 'https') or parsed.hostname not in ('instagram.com', 'www.instagram.com')
            or parsed.username or parsed.password or parsed.port not in (None, 80, 443) or not match):
        raise ValueError('请打开 Instagram 帖子或 Reels 详情页，暂不批量采集主页或 Stories')
    return f'https://www.instagram.com/p/{match[1]}/'


def trusted_url(value, metadata=False):
    parsed = urlparse(str(value))
    host = (parsed.hostname or '').lower()
    domains = (*CDN_DOMAINS, 'instagram.com') if metadata else CDN_DOMAINS
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not any(host == d or host.endswith('.' + d) for d in domains)):
        raise ValueError('Instagram 素材地址不受支持，请重新解析')
    return str(value)  # Signed query bytes must not be normalized.


def dimension(value):
    return value if type(value) is int and 0 < value < 1000000 else 0


def positive_duration(value):
    if type(value) not in (int, float, str):
        return None
    try:
        value = float(value)
    except (ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def media_bytes(value):
    if type(value) is str and re.fullmatch(r'[0-9]{1,16}', value):
        value = int(value)
    return value if type(value) is int and 0 < value <= 9007199254740991 else None


def normalized_post(raw, shortcode):
    from yt_dlp.extractor.instagram import _id_to_pk
    raw = copy.deepcopy(raw)
    expected = str(_id_to_pk(shortcode))
    if (not isinstance(raw, dict) or raw.get('code') != shortcode
            or str(raw.get('pk') or raw.get('id')).split('_')[0] != expected):
        raise ValueError('Instagram 帖子数据已变化，请重新解析')
    raw['pk'] = expected
    raw.setdefault('caption', None)
    if not isinstance(raw.get('user'), dict):
        raw['user'] = {}
    raw['user'].setdefault('pk', '')
    rows = raw.get('carousel_media') if 'carousel_media' in raw else [raw]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError('Instagram 轮播数据不完整，请等待页面加载或登录后重新解析')
    count = raw.get('carousel_media_count')
    if count is not None and (type(count) is not int or count != len(rows)):
        raise ValueError('Instagram 轮播数据不完整，请等待页面加载或登录后重新解析')
    for field in ('music_metadata', 'clips_metadata', 'story_music_stickers'):
        raw.pop(field, None)
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Instagram 素材数据不完整')
        key = str(row.get('pk') or row.get('id') or '').split('_')[0]
        if not re.fullmatch(r'\d{1,30}', key) or key in seen:
            raise ValueError('Instagram 素材编号无效或重复，请重新解析')
        row['pk'] = key
        seen.add(key)
        variants = (row.get('image_versions2') or {}).get('candidates')
        if not isinstance(variants, list) or not variants or len(variants) > 32:
            raise ValueError('Instagram 轮播素材缺失，请等待页面加载或登录后重新解析')
        for variant in variants:
            trusted_url(variant['url'])
            variant['width'], variant['height'] = dimension(variant.get('width')), dimension(variant.get('height'))
        variants.sort(key=lambda v: v['width'] * v['height'], reverse=True)
        videos = row.get('video_versions') or []
        if not isinstance(videos, list) or len(videos) > 32 or (row.get('media_type') == 2 and not videos):
            raise ValueError('Instagram 视频地址缺失，请等待播放器加载或登录后重新解析')
        for video in videos:
            trusted_url(video['url'])
        # gallery-dl chooses the last video candidate, the first image candidate.
        videos.sort(key=lambda v: dimension(v.get('width')) * dimension(v.get('height')))
        if videos:
            row['video_versions'] = videos
        for field in ('original_width', 'original_height'):
            row[field] = dimension(row.get(field))
        for field in ('music_metadata', 'clips_metadata', 'story_music_stickers'):
            row.pop(field, None)
    return raw


def guarded_ydl(ydl, check_cancel):
    from yt_dlp.networking._urllib import UrllibRH
    from urllib.request import BaseHandler

    class Guard(BaseHandler):
        handler_order = 100
        def http_request(self, request):
            check_cancel()
            trusted_url(request.full_url)
            return request
        https_request = http_request

    director = ydl._request_director
    handler = next((row for row in director.handlers.values() if isinstance(row, UrllibRH)), None)
    if handler is None:
        raise RuntimeError('缺少 yt-dlp 安全传输组件，请检查 Python 环境')
    create = handler._create_instance
    def opener(*args, **kwargs):
        instance = create(*args, **kwargs)
        instance.add_handler(Guard())
        return instance
    handler._create_instance = opener
    # The guard must run on every redirect, not merely on the initial media URL.
    for key, row in list(director.handlers.items()):
        if row is not handler:
            row.close()
            del director.handlers[key]


class InstagramPost:
    def __init__(self, collector, url):
        self.collector, self.url = collector, post_url(url)
        self.code = self.url.rstrip('/').split('/')[-1]

    def read(self):
        from gallery_dl.extractor.instagram import InstagramPostExtractor
        from gallery_dl.extractor.common import Message
        collector = self.collector
        context = (collector.request.get('context') or {}).get('instagram') or {}
        raw = context.get('post') if context.get('shortcode') == self.code else None
        proxy = str((collector.request.get('settings') or {}).get('proxy') or '')
        options = {'cookies': None, 'cookies-from-browser': None, 'cookies-update': False,
                   'proxy-env': False, 'proxy': '' if proxy.startswith('socks') else proxy,
                   'user-cache': 'memory', 'write-pages': False, 'timeout': (10, 20),
                   'retries': 1, 'previews': False, 'audio': False, 'static-videos': True,
                   'warn-images': False, 'warn-videos': False}
        owner = self

        class Extractor(InstagramPostExtractor):
            def config(self, key, default=None):
                return options.get(key, default)

            def _get_auth_info(self):
                return None, None

            def sleep(self, seconds, reason):
                collector.cancel.wait(max(0, seconds))
                collector.check_cancel()

            def wait(self, seconds=None, until=None, adjust=1.0, reason='rate limit'):
                if seconds is None:
                    if hasattr(until, 'timestamp'):
                        until = until.timestamp()
                    seconds = float(until) - time.time()
                collector.log('Instagram 要求等待后重试，可随时取消任务')
                self.sleep(max(0, float(seconds) + adjust), reason)

            def posts(self):
                if raw is not None:
                    values = [raw]
                    collector.log('[解析] 复用当前 Instagram 帖子数据，使用 gallery-dl 解析素材')
                else:
                    if not any(cookie.name == 'sessionid' and cookie.value and not cookie.is_expired() for cookie in self.cookies):
                        raise ValueError('页面未提供完整 Instagram 数据，请等待页面加载或在应用中登录后重新解析')
                    collector.log('[解析] 使用当前登录会话补取 Instagram 帖子数据')
                    values = super().posts()
                for value in values:
                    owner.post = normalized_post(value, owner.code)
                    yield copy.deepcopy(owner.post)

        ex = Extractor(re.match(InstagramPostExtractor.pattern, self.url))
        self.rows = []
        try:
            ex.initialize()
            if collector.request.get('userAgent'):
                ex.session.headers['User-Agent'] = str(collector.request['userAgent'])
            cookie_file = collector.request.get('cookieFile')
            if cookie_file:
                jar = http.cookiejar.MozillaCookieJar()
                jar.load(cookie_file, ignore_discard=True, ignore_expires=False)
                for cookie in jar:
                    host = cookie.domain.lstrip('.').lower()
                    if host == 'instagram.com' or host.endswith('.instagram.com'):
                        ex.cookies.set_cookie(cookie)
            if proxy.startswith('socks'):
                from socks_transport import install_socks_transport
                install_socks_transport(ex.session, proxy)
            send = ex.session.send
            def guarded_send(request, **kwargs):
                collector.check_cancel()
                trusted_url(request.url, metadata=True)
                kwargs['stream'] = True
                response = send(request, **kwargs)
                trusted_url(response.url, metadata=True)
                if '/accounts/login' in urlparse(response.url).path or '/challenge/' in urlparse(response.url).path:
                    response.close()
                    raise ValueError('Instagram 需要登录或验证，请先在应用页面完成')
                chunks, size, started = [], 0, time.monotonic()
                try:
                    for chunk in response.iter_content(65536):
                        collector.check_cancel()
                        size += len(chunk)
                        if size > 5 * 1024 * 1024 or time.monotonic() - started > 30:
                            raise ValueError('Instagram 数据响应过大或过慢，请稍后重试')
                        chunks.append(chunk)
                    response._content = b''.join(chunks)
                    response._content_consumed = True
                finally:
                    response.close()
                return response
            ex.session.send = guarded_send
            for kind, _url, data in ex:
                collector.check_cancel()
                if kind == Message.Url:
                    self.rows.append(copy.deepcopy(data))
        finally:
            if ex.session is not None:
                ex.session.close()
        expected = self.post.get('carousel_media') or [self.post]
        if (len(self.rows) != len(expected) or
                [row['num'] for row in self.rows] != list(range(1, len(expected) + 1)) or
                [str(row['media_id']) for row in self.rows] != [str(row['pk']) for row in expected]):
            raise ValueError('Instagram 返回素材不完整，请重新解析')
        description = str((self.post.get('caption') or {}).get('text') or '')
        author = self.post.get('user') or {}
        self.author = str(author.get('username') or author.get('full_name') or '')
        self.title = description.splitlines()[0][:100] if description else f'Instagram {self.code}'
        self.description = description
        return self

    def image_post(self):
        raw_rows = self.post.get('carousel_media') or [self.post]
        images = []
        for row, raw in zip(self.rows, raw_rows):
            if row.get('video_url'):
                continue
            variants = raw['image_versions2']['candidates']
            images.append(XhsImage(row['num'], trusted_url(row['display_url']),
                                   tuple(dict.fromkeys(trusted_url(v['url']) for v in variants[1:])),
                                   dimension(row.get('width')), dimension(row.get('height'))))
        return XhsPost(self.code, self.title, self.description, self.author, self.url, tuple(images))


class InstagramImages(XhsImageDownloader):
    def _check_cancelled(self):
        self.check_cancel()

    def _source_label(self, image, candidate_index, total):
        return 'Instagram 源图片' if candidate_index == 0 else 'Instagram 备用图片'

    def _post_folder(self, post, output_dir):
        return output_dir.resolve() / f'Instagram [{post.note_id}]'

    def _download_one(self, ydl, post, image, folder):
        if not getattr(ydl, '_instagram_guard', False):
            guarded_ydl(ydl, self.check_cancel)
            ydl._instagram_guard = True
        return super()._download_one(ydl, post, image, folder)

    @staticmethod
    def _write_summary(post, folder, *, check_cancel=None):
        data = {'site': 'instagram', 'shortcode': post.note_id, 'title': post.title,
                'author': post.author, 'source_url': post.source_url}
        write_image_summary(folder / 'post.json', data, [{'index': image.index} for image in post.images],
                            ('site', 'shortcode'), check_cancel)


def video_info(post, row, ydl):
    from yt_dlp.extractor.common import InfoExtractor
    from yt_dlp.utils import parse_duration
    formats = []
    raw = (post.post.get('carousel_media') or [post.post])[row['num'] - 1]
    duration = positive_duration(raw.get('video_duration'))
    manifest_duration, sizes = None, {}
    manifest = row.get('_ytdl_manifest_data')
    if isinstance(manifest, str) and len(manifest) < 1000000:
        try:
            xml = ElementTree.fromstring(manifest)
            manifest_duration = positive_duration(parse_duration(xml.get('mediaPresentationDuration')))
            periods = xml.findall('{*}Period')
            if not manifest_duration and len(periods) == 1:
                manifest_duration = positive_duration(parse_duration(periods[0].get('duration')))
            # Bind Facebook's byte counts to the exact representation and URL,
            # not to dimensions shared by several different quality levels.
            for representation in xml.findall('.//{*}Representation'):
                base = representation.find('{*}BaseURL')
                size = media_bytes(representation.get('FBContentLength'))
                if base is not None and base.text and size:
                    key = (f'dash-{representation.get("id")}', base.text.strip())
                    sizes[key] = size if key not in sizes or sizes[key] == size else None
            formats, _subtitles = InfoExtractor(ydl)._parse_mpd_formats_and_subtitles(
                xml, mpd_id='dash', mpd_base_url=post.url)
        except ElementTree.ParseError:
            formats = []
    for value in formats:
        for key in ('url', 'fragment_base_url'):
            if value.get(key):
                trusted_url(value[key])
        for fragment in value.get('fragments') or []:
            target = fragment.get('url') or urljoin(value.get('fragment_base_url') or '', fragment.get('path') or '')
            trusted_url(target)
        size = sizes.get((value.get('format_id'), value.get('url')))
        if size and not value.get('fragments'):
            value['filesize'] = size
    seen = set()
    for index, variant in enumerate(reversed(raw.get('video_versions') or [])):
        url = trusted_url(variant['url'])
        if url in seen:
            continue
        seen.add(url)
        quality = f'{dimension(variant.get("width"))}x{dimension(variant.get("height"))}'
        formats.append({'format_id': f'http-{quality}-{variant.get("type", 0)}', 'url': url, 'ext': 'mp4',
                        'width': dimension(variant.get('width')) or None,
                        'height': dimension(variant.get('height')) or None,
                        'filesize': media_bytes(variant.get('file_size')) or media_bytes(variant.get('content_length')),
                        'vcodec': 'unknown', 'acodec': 'unknown'})
    if not formats:
        raise ValueError('尚未取得 Instagram 可下载视频地址，请重新解析')
    return {'id': f'{post.code}_{row["media_id"]}', 'title': post.title if len(post.rows) == 1 else f'{post.title} - video {row["num"]}',
            'extractor': 'Instagram', 'extractor_key': 'Instagram', 'webpage_url': post.url,
            'uploader': post.author, 'description': post.description, 'thumbnail': trusted_url(row['display_url']),
            'duration': duration or manifest_duration, 'formats': formats,
            'http_headers': {'Referer': 'https://www.instagram.com/'}}


def inspect_post(collector, url):
    import yt_dlp
    from collector_core import normalise_video
    post = InstagramPost(collector, url).read()
    images = post.image_post()
    items = []
    if images.images:
        raw_rows = post.post.get('carousel_media') or [post.post]
        items.append({'id': post.code, 'kind': 'images', 'url': post.url,
                      'title': post.title, 'author': post.author, 'description': post.description,
                      'thumbnail': images.images[0].url,
                      'images': [{**asdict(image),
                                  'preview_url': raw_rows[image.index - 1]['image_versions2']['candidates'][-1]['url']}
                                 for image in images.images]})
    if any(row.get('video_url') for row in post.rows):
        with yt_dlp.YoutubeDL(collector.options(post.url)) as ydl:
            guarded_ydl(ydl, collector.check_cancel)
            for row in post.rows:
                if not row.get('video_url'):
                    continue
                info = ydl.process_ie_result(video_info(post, row, ydl), download=False)
                item = normalise_video(info, post.url)
                item['mediaId'] = str(row['media_id'])
                items.append(item)
    return {'site': 'instagram', 'url': post.url, 'items': items}


def download_post(collector, url, folder):
    import yt_dlp
    from collector_core import selected_video_format
    post = InstagramPost(collector, url).read()
    settings = collector.request.get('settings') or {}
    if collector.request.get('kind') == 'images':
        wanted = collector.request.get('indices') or []
        if not wanted or any(type(index) is not int for index in wanted):
            raise ValueError('请至少选择一张有效图片')
        images = tuple(image for image in post.image_post().images if image.index in wanted)
        if set(wanted) != {image.index for image in images}:
            raise ValueError('Instagram 图片选择已失效，请重新解析')
        writer = InstagramImages(
            cookie_file=Path(collector.request['cookieFile']) if collector.request.get('cookieFile') else None,
            proxy=str(settings.get('proxy') or ''), progress=collector.log, cancel_event=collector.cancel,
            user_agent=collector.request.get('userAgent'), image_format=settings.get('imageFormat', 'original'),
            image_quality=settings.get('imageQuality', 95),
            transfer_progress=lambda data: collector.emit('progress', data))
        writer.check_cancel = collector.check_cancel
        _, paths = writer.download_post(replace(post.image_post(), images=images), folder)
        return {'count': len(paths), 'folder': str(paths[0].parent)}
    row = next((row for row in post.rows if row.get('video_url') and
                str(row['media_id']) == str(collector.request.get('mediaId') or '')), None)
    if row is None:
        raise ValueError('Instagram 视频编号已变化，请重新解析')
    options = collector.options(post.url)
    identifier = str(collector.request.get('formatId') or '')
    with yt_dlp.YoutubeDL(options) as ydl:
        guarded_ydl(ydl, collector.check_cancel)
        info = video_info(post, row, ydl)
        if identifier and not settings.get('audioOnly'):
            if identifier not in {str(row.get('format_id')) for row in info['formats']}:
                raise ValueError('Instagram 画质已失效，请重新解析')
            ydl.params['format'] = selected_video_format(post.url, identifier, bool(collector.request.get('formatHasAudio')))
        collector.emit('progress', {'status': 'video-connecting', 'percent': None, 'speed': None})
        ydl.process_ie_result(info, download=True)
    collector.check_cancel()
    return {'count': 1, 'folder': str(folder)}
