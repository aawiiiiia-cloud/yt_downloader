import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests
from PIL import Image
from collector_core import Collector, checked_url
from taobao_adapter import product_url, image_url, original_url

URL = 'https://chaoshi.detail.tmall.com/item.htm?id=1070500473161&skuId=6120992941508'


def context():
    return {'itemId': '1070500473161', 'title': '隔离测试商品', 'groups': {
        'main': [{'url': 'https://img.alicdn.com/a.jpg_800x800q90.jpg', 'width': 800, 'height': 800},
                 {'url': 'https://img.alicdn.com/b.jpg'}],
        'sku': [{'url': 'https://img.alicdn.com/red.jpg', 'name': '红色'},
                {'url': 'https://img.alicdn.com/red.jpg_80x80.jpg', 'name': '红色套装'}],
        'detail': [{'url': 'https://img.alicdn.com/long.png'}, {'url': 'data:image/png;base64,ignored'},
                   {'url': 'https://evil.invalid/recommend.jpg'}]}}


def service(data=None, **options):
    return Collector({'url': URL, 'context': context() if data is None else data, **options}, lambda *_: None)


def image_bytes(mode='RGB', fmt='PNG'):
    out=io.BytesIO();Image.new(mode,(9,7)).save(out,format=fmt);return out.getvalue()


def response(data=b'', status=200, **headers):
    value=requests.Response();value.status_code=status
    value.headers.update({'Content-Type':'image/png','Content-Length':str(len(data)),**headers})
    class Raw(io.BytesIO):
        def read(self, size=-1, **_kwargs): return super().read(size)
    value.raw=Raw(data);return value


class TaobaoTests(unittest.TestCase):
    def test_product_urls_retain_original_host_and_selected_sku(self):
        for host in ('item.taobao.com','detail.tmall.com','chaoshi.detail.tmall.com'):
            value=f'https://{host}/item.htm?id=123&skuId=456&spm=tracking'
            self.assertEqual(checked_url(value),f'https://{host}/item.htm?id=123&skuId=456')
        for value in ('https://detail.tmall.com.evil.invalid/item.htm?id=123','https://tmall.com/item.htm?id=123','https://item.taobao.com/?id=123','https://user:pw@item.taobao.com/item.htm?id=123'):
            with self.assertRaises(ValueError):product_url(value)

    def test_cdn_original_candidate_preserves_queries_and_unknown_filenames(self):
        self.assertEqual(original_url('https://img.alicdn.com/a.jpg_800x800q90.jpg?sig=keep'),'https://img.alicdn.com/a.jpg?sig=keep')
        self.assertEqual(original_url('https://img.alicdn.com/a_name.jpg'),'https://img.alicdn.com/a_name.jpg')
        for value in ('file:///C:/a.jpg','https://127.0.0.1/a.jpg','https://img.alicdn.com.evil.invalid/a.jpg','https://img.alicdn.com/a.mp4','https://u:p@img.alicdn.com/a.jpg'):
            with self.assertRaises(ValueError):image_url(value)

    def test_browser_snapshot_is_required_and_bound_to_the_product(self):
        for data in ({}, {'itemId':'other'}, {**context(),'mismatch':True}):
            with mock.patch('requests.Session.send',side_effect=AssertionError('network forbidden')):
                with self.assertRaises(ValueError):service(data).inspect()

    def test_classification_and_sku_aliases_are_preserved_without_foreign_images(self):
        result=service().inspect();self.assertEqual([x['category'] for x in result['items']],['main','sku','detail'])
        self.assertEqual(result['items'][1]['images'][0]['names'],['红色','红色套装'])
        self.assertEqual(len(result['items'][2]['images']),1)
        self.assertNotIn('mp4',json.dumps(result));self.assertNotIn('evil.invalid',json.dumps(result))

    def test_thumbnail_dimensions_are_never_reported_as_original_dimensions(self):
        image=service().inspect()['items'][0]['images'][0]
        self.assertIsNone(image['width']);self.assertIsNone(image['height'])
        self.assertEqual(image['url'],'https://img.alicdn.com/a.jpg')
        self.assertIn('_800x800',image['preview_url'])

    def test_missing_details_are_explicit_and_empty_pages_do_not_succeed(self):
        data=context();data['groups']['detail']=[]
        self.assertIn('详情图',service(data).inspect()['warnings'][0])
        data['groups']={}
        with self.assertRaisesRegex(ValueError,'尚未读取'):service(data).inspect()

    def test_dom_display_variant_is_retained_when_json_original_arrives_first(self):
        data=context();data['groups']['main']=[{'url':'https://img.alicdn.com/a.jpg'},
            {'url':'https://img.alicdn.com/a.jpg_800x800q90.jpg','width':800,'height':800}]
        images=service(data).inspect()['items'][0]['images']
        self.assertEqual(len(images),1);image=images[0]
        self.assertEqual(image['url'],'https://img.alicdn.com/a.jpg')
        self.assertEqual(image['preview_url'],'https://img.alicdn.com/a.jpg_800x800q90.jpg')
        self.assertIn(image['preview_url'],image['fallback_urls'])
        self.assertIsNone(image['width']);self.assertIsNone(image['height'])

    def test_current_display_variant_survives_original_first_dom_attributes_and_keeps_signatures(self):
        original='https://img.alicdn.com/a.jpg?sig=keep'
        preview='https://img.alicdn.com/a.jpg_800x800q90.jpg?sig=keep'
        data=context();data['groups']['main']=[{'url':original,'previewUrl':preview}]
        image=service(data).inspect()['items'][0]['images'][0]
        self.assertEqual(image['preview_url'],preview);self.assertEqual(image['url'],original)

    def test_foreign_mismatched_and_tiny_previews_cannot_replace_a_clearer_image(self):
        for preview in ('https://evil.invalid/a.jpg_800x800.jpg','https://img.alicdn.com/other.jpg_800x800.jpg',
                        'https://img.alicdn.com/a.jpg_80x80.jpg','https://img.alicdn.com/a.jpg_800x800.jpg?sig=different'):
            with self.subTest(preview=preview):
                data=context();data['groups']['main']=[{'url':'https://img.alicdn.com/a.jpg','previewUrl':preview}]
                image=service(data).inspect()['items'][0]['images'][0]
                self.assertEqual(image['preview_url'],image['url'])

    def test_a_merged_display_variant_remains_a_download_fallback_after_the_original_fails(self):
        original='https://img.alicdn.com/a.jpg';preview=original+'_800x800q90.jpg'
        data=context();data['groups']['main']=[{'url':original},{'url':preview}]
        calls=[]
        def get(url,**_kwargs):
            calls.append(url);return response(image_bytes()) if url==preview else response(b'forbidden',403)
        with tempfile.TemporaryDirectory() as folder,mock.patch('requests.Session.get',side_effect=get):
            result=service(data,kind='images',mediaId='main',indices=[1],settings={'saveDir':folder}).download()
            self.assertEqual(result['count'],1);self.assertEqual(calls[0],original);self.assertEqual(calls[-1],preview)
            with Image.open(next(Path(folder).rglob('01.png'))) as image:self.assertEqual(image.size,(9,7))

    def test_selected_image_is_decoded_and_saved_in_product_category_folder(self):
        png=image_bytes()
        with tempfile.TemporaryDirectory() as folder,mock.patch('requests.Session.get',side_effect=lambda *_a,**_k:response(png)) as get:
            worker=service(kind='images',mediaId='main',indices=[2],settings={'saveDir':folder})
            result=worker.download();files=list(Path(folder).rglob('*.png'))
            self.assertEqual(result['count'],1);self.assertEqual([p.name for p in files],['02.png'])
            self.assertEqual(files[0].parent.name,'主图');self.assertEqual(files[0].read_bytes(),png)
            with Image.open(files[0]) as im:im.load();self.assertEqual(im.size,(9,7))
            self.assertEqual(get.call_count,1)

    def test_original_failure_falls_back_and_format_is_detected_from_bytes(self):
        png=image_bytes();calls=[]
        def get(url,**kwargs):
            calls.append(url)
            return response(png) if '_800x800' in url else response(b'forbidden',403)
        with tempfile.TemporaryDirectory() as folder,mock.patch('requests.Session.get',side_effect=get):
            result=service(kind='images',mediaId='main',indices=[1],settings={'saveDir':folder}).download()
            self.assertEqual(result['count'],1);self.assertTrue(list(Path(folder).rglob('01.png')))
            self.assertEqual(len(calls),3);self.assertIn('_800x800',calls[-1])

    def test_html_errors_and_truncated_images_cannot_be_saved(self):
        for data in (b'<html>login</html>',image_bytes()[:25]):
            with tempfile.TemporaryDirectory() as folder,mock.patch('requests.Session.get',side_effect=lambda *_a,**_k:response(data)):
                with self.assertRaises(Exception):service(kind='images',mediaId='main',indices=[2],settings={'saveDir':folder}).download()
                self.assertFalse(list(Path(folder).rglob('*.png')));self.assertFalse(list(Path(folder).rglob('*.part')))

    def test_redirect_to_local_or_foreign_host_is_rejected_before_requesting_it(self):
        calls=[]
        def get(url,**_kwargs):
            calls.append(url);return response(status=302,Location='http://127.0.0.1/private.jpg')
        with tempfile.TemporaryDirectory() as folder,mock.patch('requests.Session.get',side_effect=get):
            with self.assertRaises(Exception):service(kind='images',mediaId='main',indices=[2],settings={'saveDir':folder}).download()
        self.assertTrue(calls);self.assertTrue(all(url.startswith('https://img.alicdn.com/') for url in calls))

    def test_cmyk_jpeg_can_be_converted_to_png(self):
        data=image_bytes('CMYK','JPEG')
        with tempfile.TemporaryDirectory() as folder,mock.patch('requests.Session.get',side_effect=lambda *_a,**_k:response(data,**{'Content-Type':'image/jpeg'})):
            service(kind='images',mediaId='sku',indices=[1],settings={'saveDir':folder,'imageFormat':'png'}).download()
            with Image.open(next(Path(folder).rglob('*.png'))) as image:self.assertEqual(image.mode,'RGB')

    def test_cancellation_and_video_or_unknown_category_requests_are_rejected(self):
        for options in ({'kind':'video'},{'kind':'images','mediaId':'unknown','indices':[1]}):
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):service(**options,settings={'saveDir':folder}).download()
        with tempfile.TemporaryDirectory() as folder:
            worker=service(kind='images',mediaId='main',indices=[1],settings={'saveDir':folder});worker.cancel.set()
            with self.assertRaises(Exception):worker.download()

    def test_sku_manifest_preserves_names_but_omits_signed_urls(self):
        data=context();data['groups']['sku'][0]['url']='https://img.alicdn.com/red.jpg?signature=private'
        with tempfile.TemporaryDirectory() as folder,mock.patch('requests.Session.get',side_effect=lambda *_a,**_k:response(image_bytes())):
            service(data,kind='images',mediaId='sku',indices=[1],settings={'saveDir':folder}).download()
            manifest=next(Path(folder).rglob('product.json')).read_text(encoding='utf8')
            self.assertIn('红色',manifest);self.assertNotIn('private',manifest);self.assertNotIn('https:',manifest)


if __name__ == '__main__':unittest.main()
