"""Images from the current, normally loaded Taobao/Tmall product page only."""
from __future__ import annotations

import http.cookiejar
import json
import re
from dataclasses import asdict
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from xhs_image_downloader import XhsImage, XhsPost, XhsImageDownloader, _safe_name

HOSTS = {'item.taobao.com', 'detail.tmall.com', 'chaoshi.detail.tmall.com'}
IMAGE_DOMAINS = ('alicdn.com', 'tbcdn.cn', 'taobaocdn.com')
CATEGORIES = {'main': '主图', 'sku': 'SKU 图', 'detail': '详情图'}


def product_url(value):
    parsed = urlparse(str(value))
    identifier = parse_qs(parsed.query).get('id', [''])[0]
    if (parsed.scheme not in {'https', 'http'} or parsed.hostname not in HOSTS or
            parsed.username or parsed.password or parsed.port not in {None, 80, 443} or
            parsed.path != '/item.htm' or not re.fullmatch(r'\d{1,30}', identifier)):
        raise ValueError('请打开淘宝、天猫或天猫超市的商品详情页')
    query = {'id': identifier}
    sku = parse_qs(parsed.query).get('skuId', [''])[0]
    if re.fullmatch(r'\d{1,30}', sku):
        query['skuId'] = sku
    return f'https://{parsed.hostname}/item.htm?{urlencode(query)}'


def image_url(value):
    parsed = urlparse('https:' + value if str(value).startswith('//') else str(value))
    host = (parsed.hostname or '').lower()
    if (parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password or
            parsed.port not in {None, 80, 443} or not any(host == d or host.endswith('.' + d) for d in IMAGE_DOMAINS) or
            not re.search(r'\.(?:jpe?g|png|gif|webp|avif|heic|heif)(?:_[^/]*)?$', parsed.path, re.I)):
        raise ValueError('图片地址不属于淘宝支持的图片素材域名或格式')
    return parsed._replace(scheme='https', netloc=host, fragment='').geturl()


def original_url(value):
    parsed = urlparse(image_url(value))
    # Only known CDN resize suffixes. Query parameters/signatures are retained.
    path = re.sub(r'(\.(?:jpe?g|png|gif|webp|avif))_(?:\d+x\d+[^/]*|q\d+[^/]*|\.webp)$', r'\1', parsed.path, flags=re.I)
    return parsed._replace(path=path).geturl()


def collections(url, context):
    url = product_url(url)
    identifier = parse_qs(urlparse(url).query)['id'][0]
    if not isinstance(context, dict) or str(context.get('itemId')) != identifier or context.get('mismatch'):
        raise ValueError('商品数据尚未加载或已切换，请等待左侧商品页加载完成后重新解析')
    groups = context.get('groups')
    if not isinstance(groups, dict) or len(json.dumps(context, ensure_ascii=False)) > 1400000:
        raise ValueError('商品图片数据无效，请重新解析')
    title = str(context.get('title') or identifier)[:240]
    result = []
    for category, label in CATEGORIES.items():
        rows = groups.get(category) or []
        if not isinstance(rows, list) or len(rows) > 400:
            raise ValueError('商品图片过多或结构已变化，请重新解析')
        images, seen = [], {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                display = image_url(row.get('url', ''))
            except ValueError:
                continue  # Ignore placeholders and foreign recommendations, never fetch them.
            alternatives = row.get('alternatives') if isinstance(row.get('alternatives'), list) else []
            candidates = []
            for value in [display, *alternatives[:5]]:
                try:
                    for candidate in (original_url(value), image_url(value)):
                        if candidate not in candidates:
                            candidates.append(candidate)
                except ValueError:
                    continue
            if not candidates:
                continue
            key = candidates[0]
            name = str(row.get('name') or '')[:160]
            if key in seen:
                if name and name not in seen[key]['names']:
                    seen[key]['names'].append(name)
                continue
            size = lambda key: row.get(key) if type(row.get(key)) is int and 0 < row[key] < 1000000 else None
            image = {**asdict(XhsImage(len(images) + 1, key, tuple(candidates[1:]),
                                      size('width') if key == display else None,
                                      size('height') if key == display else None)),
                     'preview_url': display, 'names': [name] if name else []}
            images.append(image); seen[key] = image
        if images:
            result.append({'id': f'{identifier}_{category}', 'kind': 'images', 'url': url,
                           'title': title, 'author': '', 'category': category, 'categoryLabel': label,
                           'mediaId': category, 'thumbnail': images[0]['preview_url'], 'images': images})
    if not result:
        raise ValueError('尚未读取到商品图片，请先在左侧完成页面加载、必要的登录或验证，再重新解析')
    return result


def inspect_product(collector, url):
    collector.check_cancel()
    items = collections(url, collector.request.get('context'))
    warnings = []
    if not any(item['category'] == 'detail' for item in items):
        warnings.append('详情图尚未读取到，请在左侧展开图文详情后重新解析。')
    collector.log('商品图片：' + '，'.join(f"{item['categoryLabel']} {len(item['images'])} 张" for item in items))
    return {'site': 'taobao', 'url': product_url(url), 'items': items, 'warnings': warnings}


class ImageStream:
    def __init__(self, response):
        self.response = response; self.headers = response.headers; self.received = 0
    def __enter__(self): return self
    def __exit__(self, *_): self.response.close()
    def read(self, size):
        data = self.response.raw.read(size, decode_content=True)
        self.received += len(data)
        if self.received > 100 * 1024 * 1024:
            raise ValueError('单张图片超过 100 MB，已停止保存')
        return data


class TaobaoImages(XhsImageDownloader):
    """Reuse atomic image verification/conversion with manually validated redirects."""
    def __init__(self, *args, category, rows, **kwargs):
        super().__init__(*args, **kwargs)
        self.category = category; self.rows = rows

    def download_post(self, post, output_dir):
        with requests.Session() as session:
            self.session = session; session.trust_env = False
            session.headers.update({'User-Agent': self.user_agent or self._ydl_options()['http_headers']['User-Agent'], 'Accept-Encoding': 'identity'})
            if self.proxy: session.proxies.update({'http': self.proxy, 'https': self.proxy})
            if self.cookie_file and self.cookie_file.is_file():
                jar = http.cookiejar.MozillaCookieJar(str(self.cookie_file))
                jar.load(ignore_discard=True, ignore_expires=False)
                session.cookies.update(jar)
            return super().download_post(post, output_dir)

    def _post_folder(self, post, output_dir):
        return output_dir.resolve() / _safe_name(f'{post.title} [{post.note_id}]', post.note_id) / CATEGORIES[self.category]

    def _download_one(self, ydl, post, image, folder):
        self.referrer = post.source_url
        return super()._download_one(self, post, image, folder)

    def urlopen(self, request):
        target = request.url
        for _ in range(5):
            self._check_cancelled()
            target = image_url(target)
            try:
                response = self.session.get(target, headers={'Referer': self.referrer}, stream=True,
                                            allow_redirects=False, timeout=(10, 20))
            except requests.Timeout as exc:
                raise TimeoutError('图片下载请求超时') from exc
            except requests.RequestException as exc:
                raise ConnectionError('图片网络连接失败，请检查网络或代理设置') from exc
            if response.status_code in {301, 302, 303, 307, 308}:
                next_url = response.headers.get('Location', '')
                response.close()
                if not next_url: raise ValueError('图片跳转地址为空')
                target = urljoin(target, next_url)
                continue
            if response.status_code != 200:
                status = response.status_code; response.close()
                raise OSError(f'HTTP Error {status}: 图片请求未成功')
            return ImageStream(response)
        raise ValueError('图片地址跳转过多')

    def _source_label(self, image, candidate_index, total):
        return '商品原图优先' if candidate_index == 0 else '商品备用图片'

    def _write_summary(self, post, folder):
        selected = {image.index for image in post.images}
        payload = {'site': 'taobao', 'item_id': post.note_id, 'title': post.title, 'category': self.category,
                   'images': [{'index': row['index'], 'names': row['names']} for row in self.rows if row['index'] in selected]}
        temp = folder / '.product.json.tmp'
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf8')
        temp.replace(folder / 'product.json')


def download_product(collector, url, folder):
    if collector.request.get('kind') != 'images':
        raise ValueError('淘宝分组仅支持商品图片')
    items = collections(url, collector.request.get('context'))
    category = collector.request.get('mediaId')
    item = next((row for row in items if row['category'] == category), None)
    if not item: raise ValueError('图片分类已变化，请重新解析')
    selected = set(collector.request.get('indices') or [])
    images = tuple(XhsImage(row['index'], row['url'], tuple(row['fallback_urls']), row['width'], row['height'])
                   for row in item['images'] if row['index'] in selected)
    if not images: raise ValueError('请至少选择一张图片')
    settings = collector.request.get('settings') or {}
    completed = 0
    def progress(_index, total, name):
        nonlocal completed
        completed += 1
        collector.emit('progress', {'status': 'downloading', 'percent': completed / total * 100, 'name': name})
    writer = TaobaoImages(category=category, rows=item['images'],
        cookie_file=Path(collector.request['cookieFile']) if collector.request.get('cookieFile') else None,
        proxy=str(settings.get('proxy') or ''), progress=collector.log, cancel_event=collector.cancel,
        image_format=settings.get('imageFormat', 'original'), image_quality=settings.get('imageQuality', 95),
        user_agent=collector.request.get('userAgent'), item_progress=progress)
    post = XhsPost(parse_qs(urlparse(url).query)['id'][0], item['title'], '', '', product_url(url), images)
    _, paths = writer.download_post(post, folder)
    collector.check_cancel()
    return {'count': len(paths), 'folder': str(paths[0].parent)}
