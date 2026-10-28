import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image
from collector_core import Collector, checked_url
from collector_errors import error_payload
from test_taobao_adapter import response
from zcool_adapter import image_url, work_url

URL = 'https://www.zcool.com.cn/work/ZNzQyNjY2ODg=.html'


def context():
    return {'workId': 'ZNzQyNjY2ODg=', 'title': '受控站酷作品', 'author': '测试作者', 'images': [
        {'id': str(i), 'urlBig': f'https://img.zcool.cn/community/{i}.jpg?x-oss-process=image/resize,w_1280&k=keep&t=keep',
         'urlSmall': f'https://img.zcool.cn/community/{i}.jpg?x-oss-process=image/resize,w_309&k=keep&t=keep',
         'width': 9, 'height': 7} for i in range(1, 29)]}


def service(data=None, **options):
    return Collector({'url': URL, 'context': context() if data is None else data, **options}, lambda *_: None)


def binary(size=(9, 7), mode='RGB', fmt='PNG'):
    stream = io.BytesIO(); Image.new(mode, size).save(stream, format=fmt); return stream.getvalue()


class ZcoolTests(unittest.TestCase):
    def test_work_identity_accepts_encoded_padding_and_strips_tracking(self):
        self.assertEqual(checked_url(URL.replace('=.html', '%3D.html') + '?tracking=1'), URL)
        self.assertEqual(work_url(URL.replace('www.', '')), URL)
        for url in ('https://www.zcool.com.cn/u/123', 'https://www.zcool.com.cn/assets/123.html',
                'https://www.zcool.com.cn.evil.invalid/work/ZNzQyNjY2ODg=.html',
                URL.replace('www.', 'user:password@www.'), URL.replace('.cn/', '.cn:8443/')):
            with self.assertRaises(ValueError): work_url(url)

    def test_current_work_context_required_and_never_fetches_page_or_recommendations(self):
        for data in ({}, {**context(), 'workId': 'Zdifferent'}, {**context(), 'mismatch': True}):
            with mock.patch('requests.Session.send', side_effect=AssertionError('no remote fallback')):
                with self.assertRaises(ValueError): service(data).inspect()

    def test_sample_shape_returns_all_28_and_never_guesses_source_dimensions(self):
        result = service().inspect(); item = result['items'][0]
        self.assertEqual(result['site'], 'zcool'); self.assertEqual(len(item['images']), 28)
        self.assertEqual([i['index'] for i in item['images']], list(range(1, 29)))
        self.assertEqual(item['author'], '测试作者')
        self.assertIsNone(item['images'][0]['width']); self.assertIsNone(item['images'][0]['height'])
        self.assertEqual(item['images'][0]['expected_width'], 9)

    def test_signed_url_is_retained_and_thumbnail_is_not_a_download_fallback(self):
        data = context(); row = data['images'][0]; row['width'] = 2968; row['height'] = 3957
        result = service(data).inspect(); image = result['items'][0]['images'][0]
        self.assertEqual(image['url'], row['urlBig']); self.assertEqual(image['fallback_urls'], ())
        self.assertIn('w_309', image['preview_url']); self.assertTrue(image['resolutionLimited'])
        self.assertIn('缩放图片', result['warnings'][0])

    def test_larger_page_provided_variant_is_used_without_fabricating_urls(self):
        data = context(); row = data['images'][0]
        row['url'] = 'https://img.zcool.cn/community/1.jpg?k=actual-page-original&t=keep'
        self.assertEqual(service(data).inspect()['items'][0]['images'][0]['url'], row['url'])

    def test_duplicate_ids_and_foreign_images_are_removed_without_size_filters(self):
        data = context(); data['images'] += [data['images'][0], {'url': 'https://evil.invalid/a.jpg'},
            {'id': 'tiny', 'url': 'https://img.zcool.cn/community/tiny.png', 'width': 1, 'height': 1}]
        images = service(data).inspect()['items'][0]['images']
        self.assertEqual(len(images), 29); self.assertEqual(images[-1]['expected_width'], 1)
        for value in ('file:///C:/private.jpg', 'http://127.0.0.1/a.jpg', 'https://img.zcool.cn.evil.invalid/a.jpg',
                'https://img.zcool.cn/community/a.mp4', 'https://user:pw@img.zcool.cn/a.jpg'):
            with self.assertRaises(ValueError): image_url(value)

    def test_single_selected_image_is_verified_saved_and_manifest_omits_credentials(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch('requests.Session.get', side_effect=lambda *_a, **_k: response(binary())) as get:
            result = service(kind='images', mediaId='ZNzQyNjY2ODg=', indices=[2], settings={'saveDir': folder}).download()
            file = next(Path(folder).rglob('*.png')); self.assertEqual(file.name, '02.png')
            with Image.open(file) as actual: self.assertEqual(actual.size, (9, 7))
            self.assertEqual(result['count'], 1); self.assertEqual(get.call_count, 1)
            manifest = next(Path(folder).rglob('work.json')).read_text(encoding='utf-8')
            self.assertNotIn('keep', manifest); self.assertNotIn('https:', manifest)
            self.assertEqual(json.loads(manifest)['images'][0]['width'], 9)

    def test_scaled_response_is_not_saved_or_converted_and_failure_remains_specific(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch('requests.Session.get', side_effect=lambda *_a, **_k: response(binary((4, 3)))):
            with self.assertRaises(Exception) as caught:
                service(kind='images', mediaId='ZNzQyNjY2ODg=', indices=[1], settings={'saveDir': folder, 'imageFormat': 'webp'}).download()
            self.assertEqual(error_payload(caught.exception)['code'], 'IMAGE_RESOLUTION_LIMITED')
            self.assertFalse([p for p in Path(folder).rglob('*') if p.is_file()])

    def test_failed_download_never_downgrades_to_preview(self):
        calls = []
        def get(url, **kwargs): calls.append(url); return response(status=403)
        with tempfile.TemporaryDirectory() as folder, mock.patch('requests.Session.get', side_effect=get):
            with self.assertRaises(Exception):
                service(kind='images', mediaId='ZNzQyNjY2ODg=', indices=[1], settings={'saveDir': folder}).download()
        self.assertTrue(calls); self.assertTrue(all('w_1280' in url and 'k=keep&t=keep' in url for url in calls))

    def test_html_and_truncated_content_cannot_be_saved(self):
        for data in (b'<html>verify</html>', binary()[:25]):
            with tempfile.TemporaryDirectory() as folder, mock.patch('requests.Session.get', side_effect=lambda *_a, **_k: response(data)):
                with self.assertRaises(Exception):
                    service(kind='images', mediaId='ZNzQyNjY2ODg=', indices=[1], settings={'saveDir': folder}).download()
                self.assertFalse([p for p in Path(folder).rglob('*') if p.is_file()])

    def test_redirect_to_local_host_is_rejected_and_query_is_preserved(self):
        calls = []
        def get(url, **kwargs): calls.append(url); return response(status=302, Location='http://127.0.0.1/a.jpg')
        with tempfile.TemporaryDirectory() as folder, mock.patch('requests.Session.get', side_effect=get):
            with self.assertRaises(Exception):
                service(kind='images', mediaId='ZNzQyNjY2ODg=', indices=[1], settings={'saveDir': folder}).download()
        self.assertTrue(all(url.startswith('https://img.zcool.cn/') for url in calls))

    def test_cmyk_conversion_and_manual_cancel_keep_existing_behavior(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch('requests.Session.get', side_effect=lambda *_a, **_k: response(binary(mode='CMYK', fmt='JPEG'))):
            service(kind='images', mediaId='ZNzQyNjY2ODg=', indices=[1], settings={'saveDir': folder, 'imageFormat': 'png'}).download()
            with Image.open(next(Path(folder).rglob('*.png'))) as actual: self.assertEqual(actual.mode, 'RGB')
        with tempfile.TemporaryDirectory() as folder:
            worker = service(kind='images', mediaId='ZNzQyNjY2ODg=', indices=[1], settings={'saveDir': folder}); worker.cancel.set()
            with self.assertRaises(Exception): worker.download()

    def test_video_wrong_work_and_invalid_selection_are_rejected(self):
        for options in ({'kind': 'video'}, {'kind': 'images', 'mediaId': 'wrong', 'indices': [1]},
                {'kind': 'images', 'mediaId': 'ZNzQyNjY2ODg=', 'indices': []}):
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError): service(**options, settings={'saveDir': folder}).download()


if __name__ == '__main__': unittest.main()
