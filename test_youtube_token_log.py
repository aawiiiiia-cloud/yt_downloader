from __future__ import annotations

import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest
from unittest import mock

import yt_dlp
from yt_dlp.extractor.youtube import YoutubeIE
from yt_dlp.extractor.youtube._base import _PoTokenContext as PoTokenContext
from yt_dlp.extractor.youtube.pot._director import (
    PoTokenRequestDirector, YoutubeIEContentProviderLogger,
)

from collector_core import Collector
from youtube_extractor import LoggedYoutubeIE


TOKEN = base64.urlsafe_b64encode(b'private-fixture-po-token').decode()
URL = 'https://www.youtube.com/watch?v=abcdefghijk'


class TokenLogTests(unittest.TestCase):
    @contextmanager
    def downloader(self):
        events = []
        collector = Collector({'url': URL}, lambda kind, data: events.append((kind, data)))
        with yt_dlp.YoutubeDL({'logger': collector, 'quiet': True, 'cachedir': False,
                'extractor_args': {'youtube': {'fetch_pot': ['always']}}}, auto_init=False) as ydl:
            collector.configure_extractors(ydl, URL)
            yield ydl.get_info_extractor('Youtube'), events

    def messages(self, events):
        return [data for kind, data in events if kind == 'log' and '[PO Token] 已取得' in data]

    def test_success_is_reported_after_upstream_returns_and_does_not_reveal_token(self):
        with self.downloader() as (extractor, events):
            def fetch(**kwargs):
                self.assertEqual(self.messages(events), [])
                return TOKEN
            with mock.patch.object(extractor, '_fetch_po_token', side_effect=fetch) as upstream:
                result = extractor.fetch_po_token(client='web_embedded', context=PoTokenContext.PLAYER,
                                                 video_id='abcdefghijk')
            self.assertEqual(result, TOKEN)
            upstream.assert_called_once()
            self.assertEqual(self.messages(events), [
                '[youtube] [PO Token] 已取得 · 视频信息（player）· web_embedded'])
            self.assertNotIn(TOKEN, json.dumps(events))

    def test_download_and_subtitle_contexts_are_distinct(self):
        with self.downloader() as (extractor, events):
            with mock.patch.object(YoutubeIE, 'fetch_po_token', return_value=TOKEN):
                extractor.fetch_po_token('web', PoTokenContext.GVS, video_id='abcdefghijk')
                extractor.fetch_po_token(client='web', context=PoTokenContext.SUBS, video_id='abcdefghijk')
            self.assertIn('视频/音频下载（gvs）', self.messages(events)[0])
            self.assertIn('字幕（subs）', self.messages(events)[1])

    def test_repeated_cache_returns_do_not_spam_but_other_clients_and_videos_are_reported(self):
        with self.downloader() as (extractor, events):
            with mock.patch.object(YoutubeIE, 'fetch_po_token', return_value=TOKEN):
                for client, video in [('web', 'abcdefghijk'), ('web', 'abcdefghijk'),
                        ('web_embedded', 'abcdefghijk'), ('web', 'other-video')]:
                    extractor.fetch_po_token(client=client, context=PoTokenContext.PLAYER, video_id=video)
            self.assertEqual(len(self.messages(events)), 3)

    def test_missing_skipped_or_failed_token_is_never_reported_as_acquired(self):
        with self.downloader() as (extractor, events):
            for result in (None, '', '   '):
                with mock.patch.object(YoutubeIE, 'fetch_po_token', return_value=result):
                    self.assertEqual(extractor.fetch_po_token(), result)
            with mock.patch.object(YoutubeIE, 'fetch_po_token', side_effect=TimeoutError('fixture timeout')):
                with self.assertRaises(TimeoutError):
                    extractor.fetch_po_token()
            self.assertEqual(self.messages(events), [])

    def test_upstream_validation_failure_has_no_success_log(self):
        with self.downloader() as (extractor, events):
            with mock.patch.object(extractor, '_fetch_po_token') as upstream:
                self.assertIsNone(extractor.fetch_po_token(context=PoTokenContext.PLAYER))
                upstream.assert_not_called()
            self.assertEqual(self.messages(events), [])

    def test_untrusted_context_and_client_cannot_inject_log_contents(self):
        with self.downloader() as (extractor, events):
            with mock.patch.object(YoutubeIE, 'fetch_po_token', return_value=TOKEN):
                extractor.fetch_po_token(client='private-client\nCookie: secret', context='secret-context')
            self.assertEqual(self.messages(events), [
                '[youtube] [PO Token] 已取得 · 其他用途（unknown）· unknown'])
            self.assertNotIn('secret', json.dumps(events))

    def test_real_http_provider_return_is_required_and_cached_returns_remain_valid(self):
        self.http_provider({'poToken': TOKEN}, success=True)

    def test_http_failure_or_empty_token_cannot_produce_a_success_log(self):
        for response, status in [({'error': 'fixture failure'}, 500), ({'poToken': ''}, 200),
                                 ({'missing': 'poToken'}, 200), ({'poToken': '%%%bad'}, 200)]:
            with self.subTest(response=response, status=status):
                self.http_provider(response, status=status, success=False)

    def http_provider(self, payload, *, status=200, success):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.reply({'version': '1.3.2'})
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.reply(payload, status)
            def reply(self, value, code=200):
                body = json.dumps(value).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.downloader() as (extractor, events):
                # Import after yt-dlp's plugin loader to avoid registering twice.
                from yt_dlp_plugins.extractor.getpot_bgutil_http import BgUtilHTTPPTP
                logger = YoutubeIEContentProviderLogger(extractor, 'pot')
                cache = mock.Mock()
                cache.get.return_value = None
                director = PoTokenRequestDirector(logger, cache)
                director.register_provider(BgUtilHTTPPTP(extractor, logger, {
                    'base_url': [f'http://127.0.0.1:{server.server_port}']}))
                extractor._pot_director = director
                result = extractor.fetch_po_token(client='web', context=PoTokenContext.PLAYER,
                    video_id='abcdefghijk', ytcfg={'INNERTUBE_CONTEXT': {'client': {'clientName': 'WEB'}}})
                self.assertEqual(len(requests), 1)
                self.assertEqual(len(self.messages(events)), int(success))
                if success:
                    self.assertEqual(result, TOKEN)
                    self.assertNotIn(TOKEN, json.dumps(events))
                    cache.get.return_value = cache.store.call_args.args[1]
                    self.assertEqual(extractor.fetch_po_token(client='web', context=PoTokenContext.PLAYER,
                        video_id='abcdefghijk'), TOKEN)
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(len(self.messages(events)), 1)
                else:
                    self.assertIsNone(result)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_collector_registration_replaces_the_default_youtube_extractor(self):
        with yt_dlp.YoutubeDL({'quiet': True, 'cachedir': False}) as ydl:
            Collector({'url': URL}, lambda *_: None).configure_extractors(ydl, URL)
            self.assertIsInstance(ydl.get_info_extractor('Youtube'), LoggedYoutubeIE)


if __name__ == '__main__':
    unittest.main()
