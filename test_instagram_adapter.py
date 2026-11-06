import copy
import io
import json
import socket
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
import yt_dlp
from collector_core import Collector, Cancelled, checked_url
from instagram_adapter import InstagramPost, normalized_post, trusted_url, video_info, guarded_ydl
from socks_transport import install_socks_transport
from xml.etree import ElementTree

URL = 'https://www.instagram.com/p/Deg1QJmknzi/'
FIXTURE = json.loads((Path(__file__).parent / 'electron/fixtures/instagram.json').read_text(encoding='utf8'))
MANIFEST = '''<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" mediaPresentationDuration="PT12.5S">
<Period><AdaptationSet mimeType="video/mp4"><Representation id="v1" width="720" height="1280" bandwidth="800000" codecs="avc1.4d401f" FBContentLength="1250000">
<BaseURL>https://fixture.cdninstagram.com/video.mp4?sig=x%2By</BaseURL></Representation></AdaptationSet>
<AdaptationSet mimeType="audio/mp4"><Representation id="a1" bandwidth="128000" codecs="mp4a.40.2" FBContentLength="200000">
<BaseURL>https://fixture.cdninstagram.com/audio.mp4</BaseURL></Representation></AdaptationSet></Period></MPD>'''


def collector(raw=None, **extra):
    return Collector({'url': URL, 'context': {'instagram': {
        'shortcode': FIXTURE['code'], 'post': copy.deepcopy(raw or FIXTURE)}}, **extra},
        lambda *_args: None)


class InstagramContracts(unittest.TestCase):
    def test_real_image_writer_uses_post_referer_and_saves_only_selected_image(self):
        from PIL import Image
        from yt_dlp.networking.common import Response
        buffer = io.BytesIO()
        Image.new('RGB', (32, 24), (20, 140, 90)).save(buffer, format='PNG')
        data = buffer.getvalue()
        calls = []

        def opened(_ydl, request):
            calls.append(request)
            return Response(io.BytesIO(data), request.url,
                            {'Content-Type': 'image/png', 'Content-Length': str(len(data))}, status=200)

        with tempfile.TemporaryDirectory() as folder, patch.object(yt_dlp.YoutubeDL, 'urlopen', opened):
            result = collector(kind='images', indices=[3], settings={'saveDir': folder}).download()
            files = list(Path(folder).rglob('*.png'))
            self.assertEqual(result['count'], 1)
            self.assertEqual([file.name for file in files], ['03.png'])
            self.assertEqual(files[0].read_bytes(), data)
            with Image.open(files[0]) as image:
                image.load()
                self.assertEqual(image.size, (32, 24))
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].headers['Referer'], URL)
            summary = json.loads((files[0].parent / 'post.json').read_text(encoding='utf8'))
            self.assertEqual(summary['site'], 'instagram')
            self.assertEqual(summary['images'][0]['index'], 3)

    def test_url_aliases_and_index(self):
        for alias in ('p', 'reel', 'reels'):
            for prefix in ('', 'heylang_ciel/', 'user.name/'):
                self.assertEqual(checked_url(URL.replace('/p/', f'/{prefix}{alias}/') + '?img_index=4'), URL)
        for url in ('https://www.instagram.com/artist/', 'https://www.instagram.com/stories/artist/123/',
                    'https://www.instagram.com:123/p/Deg1QJmknzi/',
                    'https://www.instagram.com/accounts/p/Deg1QJmknzi/',
                    'https://www.instagram.com/share/p/Deg1QJmknzi/'):
            with self.assertRaises(ValueError):
                checked_url(url)

    def test_page_reuse_does_not_request_network_or_mutate_snapshot(self):
        task = collector()
        original = copy.deepcopy(task.request)
        with patch.object(requests.Session, 'send', side_effect=AssertionError('unexpected request')):
            parsed = InstagramPost(task, URL).read()
        self.assertEqual(task.request, original)
        self.assertEqual([row['media_id'] for row in parsed.rows], ['101','102','103'])
        self.assertEqual([row['num'] for row in parsed.rows], [1,2,3])
        images = parsed.image_post().images
        self.assertEqual([image.index for image in images], [1,3])
        self.assertIn('large.jpg?sig=fixture%2Btest', images[0].url)
        self.assertEqual(images[1].width, 0)
        self.assertEqual(parsed.rows[1]['video_url'], 'https://fixture.cdninstagram.com/102-large.mp4')

    def test_public_results_have_full_image_selection_and_no_video_transfer_urls(self):
        with patch.object(requests.Session, 'send', side_effect=AssertionError('unexpected request')):
            result = collector().inspect()
        self.assertEqual(result['site'], 'instagram')
        self.assertEqual([image['index'] for image in result['items'][0]['images']], [1,3])
        self.assertEqual(result['items'][1]['mediaId'], '102')
        self.assertEqual(max(row['height'] or 0 for row in result['items'][1]['formats']), 1280)
        self.assertNotIn('url', result['items'][1]['formats'][0])

    def test_missing_child_duplicate_identity_and_count_are_rejected(self):
        cases = []
        raw = copy.deepcopy(FIXTURE); raw['carousel_media_count'] = 4; cases.append(raw)
        raw = copy.deepcopy(FIXTURE); raw['carousel_media'][2]['pk'] = '101'; cases.append(raw)
        raw = copy.deepcopy(FIXTURE); raw['carousel_media'][2]['image_versions2'] = {}; cases.append(raw)
        raw = copy.deepcopy(FIXTURE); raw['carousel_media'][1]['video_versions'] = []; cases.append(raw)
        raw = copy.deepcopy(FIXTURE); raw['pk'] = '999'; cases.append(raw)
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalized_post(raw, FIXTURE['code'])

    def test_urls_reject_ssrf_and_preserve_signed_bytes(self):
        signed = 'https://cdn.cdninstagram.com/a.jpg?sig=a%2Bb%2Fc&oe=12'
        self.assertEqual(trusted_url(signed), signed)
        for url in ('https://localhost/a', 'https://127.0.0.1/a', 'file:///private',
                    'https://cdninstagram.com.evil.invalid/a', 'https://user:pass@cdninstagram.com/a',
                    'https://cdninstagram.com:12/a', 'http://cdninstagram.com/a'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                trusted_url(url)

    def test_manifest_parsing_preserves_audio_and_mp4_fallback(self):
        raw = copy.deepcopy(FIXTURE)
        raw['carousel_media'][1].pop('video_duration')
        raw['carousel_media'][1]['video_dash_manifest'] = MANIFEST
        post = InstagramPost(collector(raw), URL).read()
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = video_info(post, post.rows[1], ydl)
        self.assertTrue(any(row['format_id'].startswith('dash-') and row.get('vcodec') == 'none' for row in info['formats']))
        self.assertTrue(any(row['format_id'].startswith('http-') for row in info['formats']))
        self.assertEqual(info['id'], 'Deg1QJmknzi_102')
        self.assertEqual(info['duration'], 12.5)
        sizes = {row['format_id']: row.get('filesize') for row in info['formats']}
        self.assertEqual(sizes['dash-v1'], 1250000)
        self.assertEqual(sizes['dash-a1'], 200000)
        self.assertTrue(all(size is None for key, size in sizes.items() if key.startswith('http-')))
        with patch.object(requests.Session, 'send', side_effect=AssertionError('unexpected request')):
            public = collector(raw).inspect()['items'][1]
        self.assertEqual(public['duration'], 12.5)
        self.assertEqual(next(row['size'] for row in public['formats'] if row['id'] == 'dash-v1'), 1250000)
        self.assertFalse(next(row['sizeEstimated'] for row in public['formats'] if row['id'] == 'dash-a1'))

    def test_duration_fallback_validation_and_single_period(self):
        for value in (None, 0, -1, True, 'NaN', float('inf'), 'broken'):
            raw = copy.deepcopy(FIXTURE)
            raw['carousel_media'][1]['video_duration'] = value
            xml = ElementTree.fromstring(MANIFEST)
            xml.attrib.pop('mediaPresentationDuration')
            xml.find('{*}Period').set('duration', 'PT37.166668S')
            raw['carousel_media'][1]['video_dash_manifest'] = ElementTree.tostring(xml, encoding='unicode')
            post = InstagramPost(collector(raw), URL).read()
            with self.subTest(value=value), yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                info = video_info(post, post.rows[1], ydl)
                self.assertAlmostEqual(info['duration'], 37.166668)
        raw['carousel_media'][1]['video_duration'] = '8.25'
        post = InstagramPost(collector(raw), URL).read()
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            self.assertEqual(video_info(post, post.rows[1], ydl)['duration'], 8.25)

    def test_invalid_sizes_stay_unknown_and_missing_metadata_remains_downloadable(self):
        for value in (0, -1, True, float('inf'), 'bad', '1.25', '9007199254740992'):
            raw = copy.deepcopy(FIXTURE)
            raw['carousel_media'][1]['video_duration'] = None
            raw['carousel_media'][1]['video_versions'][0]['file_size'] = value
            xml = ElementTree.fromstring(MANIFEST)
            xml.attrib.pop('mediaPresentationDuration')
            for representation in xml.findall('.//{*}Representation'):
                representation.set('FBContentLength', str(value))
            raw['carousel_media'][1]['video_dash_manifest'] = ElementTree.tostring(xml, encoding='unicode')
            post = InstagramPost(collector(raw), URL).read()
            with self.subTest(value=value), yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                info = video_info(post, post.rows[1], ydl)
                self.assertIsNone(info['duration'])
                self.assertTrue(info['formats'])
                self.assertTrue(all(not row.get('filesize') for row in info['formats']))

    def test_sizes_bind_to_representation_not_dimensions_and_http_sizes_are_separate(self):
        xml = ElementTree.fromstring(MANIFEST)
        adaptation = xml.find('.//{*}AdaptationSet')
        other = copy.deepcopy(adaptation.find('{*}Representation'))
        other.set('id', 'v2')
        other.set('FBContentLength', '3000000')
        other.find('{*}BaseURL').text = 'https://fixture.cdninstagram.com/other.mp4'
        adaptation.append(other)
        raw = copy.deepcopy(FIXTURE)
        raw['carousel_media'][1]['video_dash_manifest'] = ElementTree.tostring(xml, encoding='unicode')
        raw['carousel_media'][1]['video_versions'][0]['file_size'] = '5000000'
        post = InstagramPost(collector(raw), URL).read()
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = video_info(post, post.rows[1], ydl)
        sizes = {row['format_id']: row.get('filesize') for row in info['formats']}
        self.assertEqual(sizes['dash-v1'], 1250000)
        self.assertEqual(sizes['dash-v2'], 3000000)
        self.assertEqual(sizes['http-720x1280-0'], 5000000)
        self.assertIsNone(sizes['http-360x640-0'])

    def test_manifest_cannot_address_local_services(self):
        raw = copy.deepcopy(FIXTURE)
        raw['carousel_media'][1]['video_dash_manifest'] = '''<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
        <Period><AdaptationSet mimeType="video/mp4"><Representation id="v1" bandwidth="800000">
        <BaseURL>http://127.0.0.1/private</BaseURL></Representation></AdaptationSet></Period></MPD>'''
        post = InstagramPost(collector(raw), URL).read()
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl, self.assertRaises(ValueError):
            video_info(post, post.rows[1], ydl)

    def test_transport_guard_checks_redirect_requests_before_io(self):
        from urllib.request import Request
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            guarded_ydl(ydl, lambda: None)
            self.assertEqual(len(ydl._request_director.handlers), 1)
            rh = next(iter(ydl._request_director.handlers.values()))
            opener = rh._create_instance({}, ydl.cookiejar)
            guard = next(handler for handler in opener.handlers if type(handler).__name__ == 'Guard')
            guard.https_request(Request('https://fixture.cdninstagram.com/a.jpg'))
            with self.assertRaises(ValueError):
                guard.http_request(Request('http://127.0.0.1/redirect'))

    def test_cancelled_inspection_does_not_enter_network(self):
        task = collector(); task.cancel.set()
        with patch.object(requests.Session, 'send', side_effect=AssertionError('unexpected request')), self.assertRaises(Cancelled):
            task.inspect()

    def test_anonymous_api_fallback_requires_application_login(self):
        task = Collector({'url': URL}, lambda *_args: None)
        with patch.object(requests.Session, 'send', side_effect=AssertionError('unexpected request')), self.assertRaisesRegex(ValueError, '登录'):
            InstagramPost(task, URL).read()

    def test_explicit_selection_and_video_identity_are_preserved(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            task = collector(kind='images', indices=[3], settings={'saveDir': folder})
            with patch('instagram_adapter.InstagramImages.download_post', return_value=(None, (Path(folder)/'03.jpg',))) as writer:
                self.assertEqual(task.download()['count'], 1)
                self.assertEqual([image.index for image in writer.call_args.args[0].images], [3])
            with self.assertRaises(ValueError):
                collector(kind='images', indices=[2], settings={'saveDir': folder}).download()
            with self.assertRaises(ValueError):
                collector(kind='video', mediaId='101', settings={'saveDir': folder}).download()


class SocksTransportContracts(unittest.TestCase):
    def test_remote_dns_uses_proxy_without_pysocks(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(5)
        self.addCleanup(listener.close)
        received, errors = [], []

        def exact(sock, size):
            data = b''
            while len(data) < size:
                chunk = sock.recv(size-len(data))
                if not chunk:
                    raise RuntimeError('unexpected disconnect')
                data += chunk
            return data

        def proxy():
            try:
                with listener.accept()[0] as client:
                    client.settimeout(5)
                    version, count = exact(client, 2)
                    self.assertEqual(version, 5); exact(client, count)
                    client.sendall(b'\x05\x00')
                    header = exact(client, 4)
                    self.assertEqual(header, b'\x05\x01\x00\x03')
                    size = exact(client, 1)[0]
                    received.append(exact(client, size).decode('ascii'))
                    exact(client, 2)
                    client.sendall(b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')
                    request = b''
                    while b'\r\n\r\n' not in request:
                        request += client.recv(4096)
                    client.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK')
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=proxy); worker.start()
        with requests.Session() as session:
            install_socks_transport(session, f'socks5h://127.0.0.1:{listener.getsockname()[1]}')
            response = session.get('http://unresolvable-instagram-fixture.invalid/test', timeout=5)
            self.assertEqual(response.text, 'OK')
        worker.join(6)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(received, ['unresolvable-instagram-fixture.invalid'])

    def test_invalid_proxy_is_not_silently_ignored(self):
        with requests.Session() as session:
            for proxy in ('socks5h://user:pass@localhost:12', 'socks5h://localhost', 'http://localhost:12'):
                with self.assertRaises(ValueError):
                    install_socks_transport(session, proxy)


if __name__ == '__main__':
    unittest.main()
