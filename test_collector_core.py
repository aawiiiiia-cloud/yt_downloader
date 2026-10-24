from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from collector_core import Collector, Cancelled, browser_post, browser_bilibili, checked_url, normalise_video
from download_core import site_for_url, youtube_single_video

URL = 'https://www.xiaohongshu.com/explore/0123456789abcdef01234567?xsec_token=fixture'


def context():
    return {'noteId': '0123456789abcdef01234567', 'note': {
        'title': '测试笔记', 'desc': '测试正文', 'user': {'nickname': '作者'},
        'imageList': [{'fileId': 'safe/image1', 'urlDefault': 'https://sns-webpic-qc.xhscdn.com/display1', 'width': 100, 'height': 200},
                      {'fileId': 'safe/image2', 'urlDefault': 'https://sns-webpic-qc.xhscdn.com/display2', 'width': 200, 'height': 300}],
    }}


class TestCollectorAdapters(unittest.TestCase):
    def test_rejects_local_or_spoofed_sources(self):
        for url in ('file:///secret', 'https://youtube.com.evil.invalid/a', 'https://user:pass@youtube.com/a'):
            with self.assertRaises(ValueError):
                checked_url(url)
        self.assertEqual(site_for_url('https://b23.tv/test'), 'bilibili')
        self.assertTrue(youtube_single_video('https://youtube.com/watch?v=abc&list=RDabc'))

    def test_browser_note_matches_current_route(self):
        post = browser_post(URL, context())
        self.assertEqual(post.title, '测试笔记')
        self.assertEqual([image.index for image in post.images], [1, 2])
        self.assertIsNone(browser_post(URL.replace('0123456789abcdef01234567', '1123456789abcdef01234567'), context()))

    def test_rejects_browser_note_with_local_network_resource(self):
        data = context()
        data['note']['imageList'][0]['urlDefault'] = 'http://127.0.0.1/private'
        with self.assertRaises(ValueError):
            browser_post(URL, data)

    def test_browser_note_upgrades_trusted_http_display_urls(self):
        data = context()
        data['note']['imageList'][0]['urlDefault'] = 'http://sns-webpic-qc.xhscdn.com/display1?imageView2=2'
        image = browser_post(URL, data).images[0]
        self.assertTrue(all(url.startswith('https://') for url in (image.url, *image.fallback_urls)))
        self.assertIn('https://sns-webpic-qc.xhscdn.com/display1?imageView2=2', image.fallback_urls)

    def test_browser_note_rejects_credentials_and_spoofed_cdn_hosts(self):
        for url in ('http://xhscdn.com.evil.invalid/display', 'https://user:secret@sns-webpic-qc.xhscdn.com/display'):
            data = context()
            data['note']['imageList'][0]['urlDefault'] = url
            with self.assertRaises(ValueError):
                browser_post(URL, data)

    def test_formats_are_metadata_without_signed_stream_urls(self):
        result = normalise_video({'id': 'a', 'formats': [
            {'format_id': '137', 'url': 'https://media.invalid/?signature=secret', 'vcodec': 'h264', 'acodec': 'none', 'height': 1080},
            {'format_id': 'drm', 'url': 'https://media.invalid', 'vcodec': 'h264', 'has_drm': True},
            {'format_id': 'bad+command', 'url': 'https://media.invalid', 'vcodec': 'h264'},
        ]}, 'https://youtube.com/watch?v=a')
        self.assertEqual([item['id'] for item in result['formats']], ['137'])
        self.assertNotIn('signature', json.dumps(result))

    def test_xhs_inspection_uses_loaded_browser_note_without_network(self):
        service = Collector({'url': URL, 'context': context()}, lambda *_args: None)
        with mock.patch('yt_dlp.YoutubeDL', side_effect=AssertionError('unexpected network')):
            result = service.inspect()
        self.assertEqual(result['items'][0]['kind'], 'images')
        self.assertEqual(len(result['items'][0]['images']), 2)

    def test_selected_xhs_indices_preserve_original_order_and_numbers(self):
        from xhs_image_downloader import XhsImageDownloader
        with tempfile.TemporaryDirectory() as raw:
            service = Collector({'url': URL, 'kind': 'images', 'context': context(), 'indices': [2],
                                 'settings': {'saveDir': raw}}, lambda *_args: None)
            with mock.patch.object(XhsImageDownloader, 'download_post', return_value=(None, (Path(raw) / '02.jpg',))) as download:
                result = service.download()
            self.assertEqual([image.index for image in download.call_args.args[0].images], [2])
            self.assertEqual(result['count'], 1)

    def test_cancelled_parse_stops_before_network(self):
        flag = threading.Event(); flag.set()
        service = Collector({'url': URL, 'context': context()}, lambda *_args: None, flag)
        with self.assertRaises(Cancelled):
            service.inspect()

    def test_bilibili_playlist_keeps_each_part(self):
        url = 'https://www.bilibili.com/video/BV123'
        service = Collector({'url': url}, lambda *_args: None)
        with mock.patch.object(service, 'options', return_value={}), mock.patch('yt_dlp.YoutubeDL') as ydl:
            ydl.return_value.__enter__.return_value.extract_info.return_value = {'entries': [
                {'id': 'part1', 'title': '第一部分', 'webpage_url': url + '?p=1', 'formats': [{'format_id':'1','url':'https://media.invalid/video','height':360}]},
                {'id': 'part2', 'title': '第二部分', 'webpage_url': url + '?p=2', 'formats': [{'format_id':'1','url':'https://media.invalid/video','height':360}]},
            ]}
            result = service.inspect()
        self.assertEqual(len(result['items']), 2)
        self.assertTrue(result['items'][1]['url'].endswith('?p=2'))

    def test_worker_protocol_is_json_and_has_no_tkinter_dependency(self):
        completed = subprocess.run([sys.executable, '-B', '-X', 'utf8', 'collector_worker.py'],
                                   input=json.dumps({'command': 'health'}) + '\n', capture_output=True,
                                   text=True, encoding='utf-8', timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        event = json.loads(completed.stdout)
        self.assertEqual(event['type'], 'result')
        self.assertIn('Pillow', event['data']['packages'])
        check = subprocess.run([sys.executable, '-B', '-c', "import collector_core,sys; assert 'tkinter' not in sys.modules"], timeout=10)
        self.assertEqual(check.returncode, 0)

    def test_browser_user_agent_is_used_for_xhs_downloads(self):
        service = Collector({'userAgent': 'FixtureChromium/145.0'}, lambda *_args: None)
        self.assertEqual(service.xhs_downloader()._ydl_options()['http_headers']['User-Agent'], 'FixtureChromium/145.0')

    def test_external_login_browser_receives_configured_proxy(self):
        import edge_login
        with mock.patch.object(edge_login.subprocess, 'Popen') as popen:
            edge_login.launch_browser('edge.exe', Path('fixture-profile'), 'https://youtube.com', proxy='http://127.0.0.1:7890')
        self.assertIn('--proxy-server=http://127.0.0.1:7890', popen.call_args.args[0])

    def test_muxed_format_does_not_add_a_second_audio_stream(self):
        with tempfile.TemporaryDirectory() as raw:
            service = Collector({'url': 'https://www.bilibili.com/video/BV123', 'formatId': '18',
                                 'formatHasAudio': True, 'settings': {'saveDir': raw}}, lambda *_args: None)
            with mock.patch.object(service, 'options', return_value={}), mock.patch('yt_dlp.YoutubeDL') as ydl:
                ydl.return_value.__enter__.return_value.download.return_value = 0
                service.download()
            self.assertEqual(ydl.call_args.args[0]['format'], '18')

    def test_bilibili_browser_player_formats_and_stale_part(self):
        data = {'videoId': 'BV123', 'part': 1, 'video': {'title': '浏览器视频', 'pages': [{'page':1,'part':'第一段'},{'page':2,'part':'第二段'}]}, 'playInfo': {
            'dash': {'video': [{'id': 16, 'baseUrl': 'https://fixture.bilivideo.com/video.m4s',
                                'mimeType': 'video/mp4', 'codecs': 'avc1', 'height': 360, 'width': 640}]}}}
        info = browser_bilibili('https://www.bilibili.com/zh-Hans/video/BV123', data, None)
        self.assertEqual(normalise_video(info, info['webpage_url'])['formats'][0]['height'], 360)
        self.assertTrue(info['parts'][1]['url'].endswith('?p=2'))
        self.assertIsNone(browser_bilibili('https://www.bilibili.com/video/BV123?p=2', data, None))

    def test_bilibili_browser_player_rejects_unrelated_stream_host(self):
        data = {'videoId': 'BV123', 'playInfo': {'dash': {'video': [
            {'id':16, 'baseUrl':'http://127.0.0.1/private', 'mimeType':'video/mp4'}]}}}
        with self.assertRaises(ValueError):
            browser_bilibili('https://www.bilibili.com/video/BV123', data, None)

    def test_empty_formats_are_not_reported_as_success(self):
        service = Collector({'url': 'https://www.bilibili.com/video/BV123'}, lambda *_args: None)
        with mock.patch.object(service, 'options', return_value={}), mock.patch('yt_dlp.YoutubeDL') as ydl:
            ydl.return_value.__enter__.return_value.extract_info.return_value = {'id':'a','title':'标题而已'}
            with self.assertRaisesRegex(RuntimeError, '可下载画质'):
                service.inspect()

    def test_youtube_download_rejects_missing_or_unverified_auth_before_creating_output(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw) / 'must-not-exist'
            for verified in (False, True):
                service = Collector({'url':'https://www.youtube.com/watch?v=fixture', 'youtubeVerified':verified,
                                     'settings':{'saveDir':str(folder)}}, lambda *_: None)
                with self.assertRaisesRegex(ValueError, 'Edge'):
                    service.download()
            self.assertFalse(folder.exists())

    def test_video_sizes_keep_estimate_flags_and_bitrate(self):
        result = normalise_video({'duration':3527,'formats':[
            {'format_id':'1','url':'https://fixture.invalid/a','filesize':340000},
            {'format_id':'2','url':'https://fixture.invalid/b','filesize_approx':420000,'tbr':800},
        ]}, 'https://youtube.com/watch?v=fixture')
        self.assertFalse(result['formats'][0]['sizeEstimated'])
        self.assertTrue(result['formats'][1]['sizeEstimated'])
        self.assertEqual(result['formats'][1]['bitrate'],800)

    def test_youtube_worker_requires_both_verified_state_and_site_auth_cookie(self):
        with tempfile.TemporaryDirectory() as raw:
            cookies = Path(raw) / 'youtube.cookies.txt'
            cookies.write_text('# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tfixture\n',encoding='utf-8')
            request = {'url':'https://www.youtube.com/watch?v=fixture','cookieFile':str(cookies),
                       'settings':{'saveDir':str(Path(raw)/'downloads')}}
            service = Collector(request,lambda *_:None)
            with self.assertRaisesRegex(ValueError,'Edge'):
                service.download()
            service.request['youtubeVerified'] = True
            with mock.patch.object(service,'options',return_value={}), mock.patch('yt_dlp.YoutubeDL') as ydl:
                ydl.return_value.__enter__.return_value.download.return_value = 0
                self.assertEqual(service.download()['count'],1)

    def test_explicit_invalid_youtube_credentials_cannot_fall_back_to_unsigned_download(self):
        events=[]
        service=Collector({'youtubeVerified':True},lambda *event:events.append(event))
        with self.assertRaisesRegex(RuntimeError,'登录已失效'):
            service.warning('The provided YouTube account cookies are no longer valid')
        self.assertEqual(events[0][0],'auth-invalid')


if __name__ == '__main__':
    unittest.main()
