"""ZCOOL media from the currently loaded work; never fabricate unsigned source URLs."""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from PIL import Image
from taobao_adapter import TaobaoImages
from xhs_image_downloader import XhsImage, XhsPost, _safe_name


class ResolutionLimited(ValueError):
    failure_code = 'IMAGE_RESOLUTION_LIMITED'


def work_id(value):
    parsed = urlparse(str(value))
    match = re.fullmatch(r'/work/(Z[A-Za-z0-9_+=-]{5,100})\.html', unquote(parsed.path))
    if (parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'www.zcool.com.cn', 'zcool.com.cn'}
            or parsed.username or parsed.password or parsed.port not in {None, 80, 443} or not match):
        raise ValueError('请打开站酷作品详情页，暂不解析主页、搜索页或收藏夹')
    return match[1]


def work_url(value):
    return f'https://www.zcool.com.cn/work/{work_id(value)}.html'


def image_url(value):
    parsed = urlparse('https:' + value if str(value).startswith('//') else str(value))
    host = (parsed.hostname or '').lower()
    if (parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password
            or parsed.port not in {None, 80, 443}
            or not any(host == d or host.endswith('.' + d) for d in ('zcool.cn', 'zcool.com.cn'))
            or not re.search(r'\.(?:jpe?g|png|gif|webp|avif|heic|heif)$', parsed.path, re.I)):
        raise ValueError('图片地址不属于站酷支持的图片素材域名或格式')
    return parsed._replace(scheme='https', netloc=host, fragment='').geturl()


def resize_width(value):
    query = parse_qs(urlparse(value).query)
    processing = query.get('x-oss-process', [''])[0] + query.get('imageMogr2', [''])[0]
    match = re.search(r'(?:^|[/,])w[_/](\d+)', processing)
    return int(match[1]) if match else None


def validate_context(url, context):
    identifier = work_id(url)
    if not isinstance(context, dict) or context.get('workId') != identifier or context.get('mismatch'):
        raise ValueError('站酷作品数据尚未就绪或已切换，请在左侧完成页面加载、登录或网站验证后重新解析')
    rows = context.get('images', [])
    if not isinstance(rows, list) or len(rows) > 400 or len(json.dumps(context, ensure_ascii=False)) > 1400000:
        raise ValueError('站酷作品图片数据无效，请重新解析')
    return identifier


def collection(url, context, *, allow_empty=False):
    identifier = validate_context(url, context)
    rows = context.get('images') or []
    images, seen = [], set()
    for row in rows:
        if not isinstance(row, dict): continue
        candidates = []
        for key in ('urlBig', 'url'):
            try:
                candidate = image_url(row.get(key) or '')
                if candidate not in candidates: candidates.append(candidate)
            except ValueError: pass
        if not candidates: continue
        # Prefer the largest actual page-provided candidate, without deleting any query parameters.
        chosen = max(candidates, key=lambda value: resize_width(value) or 1000000)
        identity = str(row.get('id') or urlparse(chosen).path)
        if identity in seen: continue
        seen.add(identity)
        size = lambda key: row.get(key) if type(row.get(key)) is int and 0 < row[key] < 1000000 else None
        expected_width, expected_height = size('width'), size('height')
        try: preview = image_url(row.get('urlSmall') or row.get('url') or chosen)
        except ValueError: preview = chosen
        limited = bool(expected_width and resize_width(chosen) and resize_width(chosen) < expected_width)
        images.append({**asdict(XhsImage(len(images) + 1, chosen)), 'preview_url': preview,
            'expected_width': expected_width, 'expected_height': expected_height,
            'resolutionLimited': limited})
    if not images:
        if allow_empty: return None
        raise ValueError('尚未读取到站酷作品正文图片，请在左侧完成页面加载、必要的登录或网站验证后重新解析')
    return {'id': identifier, 'mediaId': identifier, 'kind': 'images', 'url': work_url(url),
        'title': str(context.get('title') or identifier)[:240], 'author': str(context.get('author') or '')[:120],
        'thumbnail': images[0]['preview_url'], 'images': images}


def inspect_work(collector, url):
    collector.check_cancel()
    item = collection(url, collector.request.get('context'), allow_empty=True)
    items = [item] if item else []
    image_warnings = []
    if item and any(row['resolutionLimited'] for row in item['images']):
        image_warnings.append('当前页面提供了缩放图片。请在左侧登录后重新解析；下载会核对实际尺寸，低于作品标注尺寸时停止保存。')
    warnings = list(image_warnings)
    from zcool_video import inspect_videos
    items.extend(inspect_videos(collector, url))
    if (collector.request.get('context') or {}).get('skippedVideos'):
        warnings.append('已跳过不支持的视频来源；当前支持站酷原生 MP4。')
    if not items: raise ValueError('尚未读取到站酷正文图片或原生视频，请完成左侧页面加载后重新解析')
    collector.log(f"站酷作品：{len(item['images']) if item else 0} 张正文图片 · {len(items) - bool(item)} 个视频")
    return {'site': 'zcool', 'url': work_url(url), 'items': items, 'warnings': warnings, 'imageWarnings': image_warnings}


class ZcoolImages(TaobaoImages):
    _image_url = staticmethod(image_url)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, category='work', **kwargs)
        self.verified = {}

    def _post_folder(self, post, output_dir):
        return output_dir.resolve() / _safe_name(f'{post.title} [站酷 {post.note_id}]', post.note_id)

    def _validate_download(self, file, image):
        with Image.open(file) as actual:
            width, height = actual.size
            if actual.getexif().get(274) in {5, 6, 7, 8}: width, height = height, width
        if image.width and image.height and (width < image.width or height < image.height):
            raise ResolutionLimited(f'图片 {image.index} 清晰度受限：实际 {width}×{height}，作品标注 {image.width}×{image.height}。请在左侧登录后重新解析，并确认网站提供的清晰度。')
        self.verified[image.index] = {'index': image.index, 'width': width, 'height': height,
            'dimensionsVerified': bool(image.width and image.height)}

    def _source_label(self, image, candidate_index, total):
        row = self.verified[image.index]
        return f"站酷页面图片 {row['width']}×{row['height']}" + ('，尺寸已核对' if row['dimensionsVerified'] else '，作品尺寸未知')

    def _write_summary(self, post, folder):
        # No signed media URLs, tokens or cookies in saved manifests.
        data = {'site': 'zcool', 'work_id': post.note_id, 'title': post.title, 'author': post.author,
            'images': [self.verified[image.index] for image in post.images]}
        temp = folder / '.work.json.tmp'
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(folder / 'work.json')


def download_work(collector, url, folder):
    validate_context(url, collector.request.get('context'))
    if collector.request.get('kind') == 'video':
        from zcool_video import download_video
        return download_video(collector, url, folder)
    if collector.request.get('kind') != 'images': raise ValueError('站酷当前仅支持作品图片')
    item = collection(url, collector.request.get('context'))
    if collector.request.get('mediaId') != item['mediaId']: raise ValueError('站酷作品已变化，请重新解析')
    selected = collector.request.get('indices') or []
    if not isinstance(selected, list) or any(type(index) is not int for index in selected):
        raise ValueError('图片选择无效，请重新选择')
    images = tuple(XhsImage(row['index'], row['url'], (), row['expected_width'], row['expected_height'])
        for row in item['images'] if row['index'] in selected)
    if not images: raise ValueError('请至少选择一张图片')
    settings = collector.request.get('settings') or {}
    completed = 0
    def progress(_index, total, name):
        nonlocal completed
        completed += 1
        collector.emit('progress', {'status': 'downloading', 'percent': completed / total * 100, 'name': name})
    writer = ZcoolImages(rows=item['images'],
        cookie_file=Path(collector.request['cookieFile']) if collector.request.get('cookieFile') else None,
        proxy=str(settings.get('proxy') or ''), progress=collector.log, cancel_event=collector.cancel,
        image_format=settings.get('imageFormat', 'original'), image_quality=settings.get('imageQuality', 95),
        user_agent=collector.request.get('userAgent'), item_progress=progress,
        transfer_progress=lambda data: collector.emit('progress', data))
    _, paths = writer.download_post(XhsPost(item['id'], item['title'], '', item['author'], item['url'], images), folder)
    collector.check_cancel()
    return {'count': len(paths), 'folder': str(paths[0].parent)}
