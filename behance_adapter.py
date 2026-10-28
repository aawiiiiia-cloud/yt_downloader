"""Behance projects via gallery-dl; downloads retain the collector's queue and writers."""
from __future__ import annotations

import copy
import http.cookiejar
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import urlparse

from xhs_image_downloader import XhsImage, XhsPost, XhsImageDownloader
from image_summary import write_image_summary


IMAGE_DOMAINS = ('behance.net',)
MEDIA_DOMAINS = ('behance.net', 'adobe.com', 'adobe.net', 'www-ccv.adobe.io')


def trusted_url(value, domains=MEDIA_DOMAINS):
    parsed = urlparse('https:' + value if str(value).startswith('//') else str(value))
    host = (parsed.hostname or '').lower()
    if (parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password
            or parsed.port not in {None, 80, 443}
            or not any(host == d or host.endswith('.' + d) for d in domains)):
        raise ValueError('Behance 素材地址不受支持，请刷新作品页后重新解析')
    return parsed._replace(scheme='https', netloc=host, fragment='').geturl()


def project_id(url):
    parsed = urlparse(url)
    if parsed.hostname not in {'www.behance.net', 'behance.net'}:
        raise ValueError('请打开 Behance 作品详情页')
    match = re.match(r'^/gallery/(\d+)(?:/|$)', parsed.path)
    if not match:
        raise ValueError('请先打开 Behance 作品详情页，暂不批量解析主页或收藏夹')
    return match[1]


class BehanceImages(XhsImageDownloader):
    """Reuse the verified, atomic image writer, including GIFs and CMYK conversion."""
    def _check_cancelled(self):
        self.check_cancel()

    def _source_label(self, image, candidate_index, total):
        return '作品源文件' if candidate_index == 0 else '作品备用图片'

    @staticmethod
    def _write_summary(post, folder, *, check_cancel=None):
        data = {'site': 'behance', 'project_id': post.note_id, 'title': post.title,
                'author': post.author, 'image_count': len(post.images), 'source_url': post.source_url}
        write_image_summary(folder / 'project.json', data, [{'index': image.index} for image in post.images], ('site', 'project_id'), check_cancel)


class BehanceProject:
    def __init__(self, collector, url):
        try:
            from gallery_dl.extractor.behance import BehanceGalleryExtractor
        except ImportError as exc:
            raise RuntimeError('缺少 gallery-dl，请检查项目 Python 环境后重新启动') from exc
        self.collector, self.url, self.id = collector, url, project_id(url)
        self.player_metadata = {}
        options = {'timeout': 20, 'retries': 1, 'sleep-429': 0, 'proxy-env': False,
                   'proxy': str((collector.request.get('settings') or {}).get('proxy') or ''),
                   'cookies': None, 'write-pages': False,
                   'modules': ['image', 'video', 'mediacollection']}

        class Extractor(BehanceGalleryExtractor):
            def config(self, key, default=None):
                return options[key] if key in options else super().config(key, default)

            def sleep(self, seconds, reason):
                collector.cancel.wait(seconds)
                collector.check_cancel()

        self.extractor = Extractor(re.match(BehanceGalleryExtractor.pattern, url))

    def __enter__(self):
        ex = self.extractor
        try:
            ex.initialize()
            cookie_file = self.collector.request.get('cookieFile')
            if cookie_file:
                jar = http.cookiejar.MozillaCookieJar()
                jar.load(cookie_file, ignore_discard=True, ignore_expires=False)
                for cookie in jar:
                    domain = cookie.domain.lstrip('.').lower()
                    if domain == 'behance.net' or domain.endswith('.behance.net'):
                        ex.cookies.set_cookie(cookie)
            send = ex.session.send

            def guarded_send(request, **kwargs):
                self.collector.check_cancel()
                request.url = trusted_url(request.url)
                response = send(request, **kwargs)
                self.collector.check_cancel()
                if urlparse(request.url).hostname == 'www-ccv.adobe.io':
                    identifier = re.search(r'/player/ccv/([^/]+)', urlparse(request.url).path)
                    if identifier and len(response.content) < 2_000_000:
                        page = response.text
                        poster = re.search(r'"posterframe"\s*:\s*"([^"]+)"', page)
                        duration = re.search(r'"duration"\s*:\s*([\d.]+)', page)
                        self.player_metadata[identifier[1]] = {
                            'thumbnail': trusted_url(html.unescape(poster[1])) if poster else None,
                            'duration': float(duration[1]) if duration else None}
                return response
            # requests uses send() for each redirect too. Page-controlled video URLs
            # cannot redirect this parser to arbitrary local or remote services.
            ex.session.send = guarded_send
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_args):
        if self.extractor.session is not None:
            self.extractor.session.close()

    def read(self):
        self.collector.check_cancel()
        context = self.collector.request.get('context') or {}
        raw = context.get('project') if str(context.get('projectId')) == self.id else None
        if isinstance(raw, dict) and str(raw.get('id')) == self.id and isinstance(raw.get('modules'), list):
            data = self.extractor._update(copy.deepcopy(raw))
            self.collector.log('已读取左侧 Behance 作品数据，使用 gallery-dl 解析素材')
        else:
            self.collector.log('正在通过 gallery-dl 获取 Behance 作品数据…')
            data = self.extractor.get_gallery_data()
        if str(data.get('id')) != self.id:
            raise ValueError('作品数据已变化，请刷新后重新解析')
        if len(data.get('modules') or []) > 300:
            raise ValueError('作品包含过多素材，请使用较小的作品页')
        # Browser JSON orders allAvailable before named size objects. gallery-dl
        # 1.32.17 iterates collection sizes as objects; normalize that ordering
        # while retaining the actual variants for previews and fallback downloads.
        modules = []
        self.skipped_external = 0
        for module in data.get('modules') or []:
            if module.get('__typename') == 'EmbedModule':
                self.skipped_external += 1
                continue
            if module.get('__typename') == 'VideoModule':
                iframe = re.search(r'\bsrc=["\']([^"\']+)', str(module.get('embed') or ''))
                if iframe:
                    try:
                        trusted_url(html.unescape(iframe[1]))
                    except ValueError:
                        self.skipped_external += 1
                        continue
            modules.append(module)
            if module.get('__typename') == 'MediaCollectionModule':
                for component in module.get('components') or []:
                    sizes = component.get('imageSizes') or {}
                    named = {key: value for key, value in sizes.items() if isinstance(value, dict) and value.get('url')}
                    variants = sizes.get('allAvailable') or []
                    if not named:
                        named = {f'available{index}': value for index, value in enumerate(variants) if isinstance(value, dict) and value.get('url')}
                    component['imageSizes'] = {**named, **({'allAvailable': variants} if named and variants else {})}
        data['modules'] = modules
        try:
            rows = self.extractor.get_images(data)
        except self.extractor.exc.AuthorizationError as exc:
            message = ('当前账号无访问权限，请检查左侧 Behance 作品访问限制'
                       if data.get('matureAccess') == 'restricted-safe'
                       else '此作品需要登录，请先在左侧完成 Behance 登录后重新解析')
            raise ValueError(message) from exc
        self.collector.check_cancel()
        if len(rows) > 500:
            raise ValueError('作品包含过多素材，请使用较小的作品页')
        self.rows = []
        for value, module in rows:
            if module.get('__typename') == 'VideoModule':
                try:
                    trusted_url(value.removeprefix('ytdl:'))
                except ValueError:
                    self.skipped_external += 1
                    continue
            self.rows.append((value, module))
        self.data = data
        if self.skipped_external:
            self.collector.log(f'已跳过 {self.skipped_external} 个外部托管视频')
        return self

    def image_post(self):
        images, seen = [], set()
        self.previews = {}
        for value, module in self.rows:
            if module.get('__typename') not in {'ImageModule', 'MediaCollectionModule'}:
                continue
            url = trusted_url(value, IMAGE_DOMAINS)
            if url in seen:
                continue
            seen.add(url)
            source = module
            for component in module.get('components') or []:
                if any(isinstance(size, dict) and urlparse(str(size.get('url'))).path.rsplit('/', 1)[-1] == urlparse(url).path.rsplit('/', 1)[-1]
                       for size in (component.get('imageSizes') or {}).values()):
                    source = component
                    break
            sizes = source.get('imageSizes') or {}
            candidates = [*(sizes.get('allAvailable') or []), *sizes.values()]
            available = [row for row in candidates if isinstance(row, dict) and row.get('url')]
            # Keep the chosen source first, then real alternatives. The last (small)
            # display variant serves previews without downloading an original twice.
            available.sort(key=lambda row: int(row.get('width') or 0), reverse=True)
            animated = urlparse(url).path.lower().endswith('.gif')
            fallback = list(dict.fromkeys(trusted_url(row['url'], IMAGE_DOMAINS) for row in available
                                         if row['url'] != url and (not animated or not re.search(r'_still|_webp', row['url']))))[:3]
            display = next((row['url'] for row in available if '/disp/' in row['url']), None)
            still = next((row['url'] for row in available if '/disp_still/' in row['url']), None) if animated else None
            preview = trusted_url(still or display, IMAGE_DOMAINS) if still or display else (fallback[-1] if fallback else url)
            if display and display != url and display not in fallback:
                fallback.append(trusted_url(display, IMAGE_DOMAINS))
            chosen = next((row for row in available if row.get('url') == url), {})
            width = chosen.get('width') or source.get('width')
            height = chosen.get('height')
            if not height and (not chosen.get('width') or chosen.get('width') == source.get('width')):
                height = source.get('height')
            images.append(XhsImage(len(images) + 1, url, tuple(fallback),
                                   width, height))
            self.previews[len(images)] = preview
        return XhsPost(self.id, str(self.data.get('name') or self.id), '',
                       ', '.join(self.data.get('owners') or []), self.url, tuple(images))

    def videos(self):
        for ordinal, (value, module) in enumerate(self.rows):
            if module.get('__typename') == 'VideoModule' and value:
                yield f'{module.get("id", ordinal)}-{ordinal}', value.removeprefix('ytdl:'), module


def inspect_project(collector, url):
    from collector_core import normalise_video
    items = []
    with BehanceProject(collector, url) as project:
        project.read()
        post = project.image_post()
        if post.images:
            items.append({'id': post.note_id, 'kind': 'images', 'title': post.title, 'author': post.author,
                          'url': url, 'thumbnail': post.images[0].url,
                          'images': [{**asdict(image), 'preview_url': project.previews[image.index]} for image in post.images]})
        def video_item(entry):
            index, (key, target, module) = entry
            collector.check_cancel()
            title = f'{post.title} · 视频 {index}'
            try:
                info = resolve_video(collector, url, target, key, title)
                meta = project.player_metadata.get(urlparse(target).path.strip('/').split('/')[0], {})
                if not info.get('thumbnail'):
                    info['thumbnail'] = meta.get('thumbnail')
                if not info.get('duration'):
                    info['duration'] = meta.get('duration')
                item = normalise_video(info, url)
                item['mediaId'] = key
                item['author'] = item['author'] or post.author
                return item
            except Exception as exc:
                collector.check_cancel()
                from collector_errors import safe_error_text
                collector.log('Behance 视频解析失败：' + safe_error_text(exc))
                return {'id': key, 'kind': 'unavailable', 'url': url, 'title': title, 'author': post.author,
                        'message': '此视频暂未取得可下载画质，请稍后重新解析。'}
        # Independent players may respond slowly. Bound concurrency and preserve
        # the work's original ordering instead of waiting for each in series.
        # yt-dlp's first plugin load is global and not thread safe.
        if any(True for _ in project.videos()):
            import yt_dlp
            with yt_dlp.YoutubeDL({'quiet': True}, auto_init=False):
                pass
        with ThreadPoolExecutor(max_workers=3) as workers:
            items.extend(workers.map(video_item, enumerate(project.videos(), 1)))
        if not items and not project.skipped_external:
            raise ValueError('当前作品没有可下载的图片或视频，请在左侧检查访问权限或登录后重试')
    return {'site': 'behance', 'url': url, 'items': items, 'skippedExternal': project.skipped_external}


def resolve_video(collector, source_url, target, key, title, download=False):
    import yt_dlp
    try:
        target = trusted_url(target)
    except ValueError as exc:
        raise ValueError('外部平台托管的视频默认不下载') from exc
    options = collector.options(target)
    options.setdefault('http_headers', {})['Referer'] = source_url
    options.update(retries=1, socket_timeout=20)
    cookie_file = options.pop('cookiefile', None)
    identifier = str(collector.request.get('formatId') or '')
    if download and identifier and not (collector.request.get('settings') or {}).get('audioOnly'):
        from collector_core import selected_video_format
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', identifier):
            raise ValueError('画质标识无效，请重新解析')
        options['format'] = selected_video_format(target, identifier, bool(collector.request.get('formatHasAudio')))
    with yt_dlp.YoutubeDL(options) as ydl:
        if cookie_file:
            jar = http.cookiejar.MozillaCookieJar()
            jar.load(cookie_file, ignore_discard=True, ignore_expires=False)
            for cookie in jar:
                ydl.cookiejar.set_cookie(copy.copy(cookie))
        collector.configure_extractors(ydl, target)
        info = ydl.extract_info(target, download=False)
        collector.check_cancel()
        if not info or info.get('_type') in {'playlist', 'multi_video'} or not info.get('formats'):
            raise ValueError('尚未取得此视频的可下载画质')
        info.update(id=f'{project_id(source_url)}_{key}', title=title)
        if download:
            ydl.process_ie_result(info, download=True)
        return info


def download_project(collector, url, folder):
    with BehanceProject(collector, url) as project:
        project.read()
        post = project.image_post()
        if collector.request.get('kind') == 'images':
            selected = set(collector.request.get('indices') or [])
            images = tuple(image for image in post.images if image.index in selected)
            if not images:
                raise ValueError('请至少选择一张图片')
            settings = collector.request.get('settings') or {}
            writer = BehanceImages(cookie_file=Path(collector.request['cookieFile']) if collector.request.get('cookieFile') else None,
                                   proxy=str(settings.get('proxy') or ''), progress=collector.log, cancel_event=collector.cancel,
                                   image_format=settings.get('imageFormat', 'original'), image_quality=settings.get('imageQuality', 95),
                                   user_agent=collector.request.get('userAgent'),
                                   transfer_progress=lambda data: collector.emit('progress', data),
                                   item_progress=lambda current, total, name: collector.emit('progress', {
                                       'status': 'downloading', 'percent': current / max(total, 1) * 100, 'name': name}))
            writer.check_cancel = collector.check_cancel
            # The shared writer reports source indices; selected images can be sparse.
            completed = 0
            def progress(_index, _total, name):
                nonlocal completed
                completed += 1
                collector.emit('progress', {'status': 'downloading', 'percent': completed / len(images) * 100, 'name': name})
            writer.item_progress = progress
            _, paths = writer.download_post(replace(post, images=images), folder)
            return {'count': len(paths), 'folder': str(paths[0].parent)}
        wanted = str(collector.request.get('mediaId') or '')
        for key, target, _module in project.videos():
            if key == wanted:
                resolve_video(collector, url, target, key, f'{post.title} · 视频 {key}', download=True)
                collector.check_cancel()
                return {'count': 1, 'folder': str(folder)}
        raise ValueError('作品视频已变化，请重新解析后下载')
